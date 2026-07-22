import ok_serial

import asyncio
import contextlib
import dataclasses
import logging
import os
import sys
import termios
import time
import typing

from ok_serial._terminal_chunker import TerminalChunker, chunk_to_bytes
from ok_serial._terminal_decorator import TerminalDecorator
from ok_serial._timeout_math import from_deadline


@dataclasses.dataclass(frozen=True)
class SerialTerminalOptions:
    match: str
    copts: ok_serial.SerialConnectionOptions
    mopts: ok_serial.SerialMonitorOptions
    raw: bool = False


def run_terminal(opts: SerialTerminalOptions):
    """Synchronous wrapper for `run_terminal_async`"""
    asyncio.run(run_terminal_async(opts))


async def run_terminal_async(opts: SerialTerminalOptions):
    """Runs an interactive terminal communicating with a serial monitor"""
    await _TerminalSession().run(opts)


class _TerminalSession:
    async def run(self, opts: SerialTerminalOptions) -> None:
        async with contextlib.AsyncExitStack() as cleanup:
            self._opts = opts
            self._event_loop = asyncio.get_running_loop()
            self._new_data_event = asyncio.Event()

            self._decorator: TerminalDecorator | None = None
            self._serial: ok_serial.SerialConnection | None = None
            self._serial_signals: ok_serial.SerialControlSignals | None = None
            self._stdin_chunks: list[bytes | str] = []
            self._serial_chunks: list[bytes | str] = []
            self._stderr_buffer: str = ""

            # if stdin and stdout are the same terminal, do Fancy Terminal Stuff
            if os.isatty(1) and os.stat(0) == os.stat(1):
                if os.stat(1) == os.stat(2):
                    patch_args = (sys.stderr, "write", self._capture_stderr)
                    patch_context = _monkeypatch_context(*patch_args)
                    cleanup.enter_context(patch_context)  # before raw mode!

                cleanup.enter_context(_raw_tty_context(0))
                cleanup.enter_context(_raw_tty_context(1))
                self._decorator = TerminalDecorator()
                cleanup.callback(self._shutdown_decorator)

            task_group = await cleanup.enter_async_context(asyncio.TaskGroup())
            task_group.create_task(self._read_from_stdin())
            task_group.create_task(self._run_serial_monitor())
            await self._main_loop()

    async def _main_loop(self) -> None:
        echo_deadline: float | None = None
        while True:
            try:
                async with asyncio.timeout(from_deadline(echo_deadline)):
                    await self._new_data_event.wait()
            except TimeoutError:
                pass

            await asyncio.sleep(0)  # let logs updates, etc. happen
            self._new_data_event.clear()

            # use Fancy Terminal if available, else relay raw data directly
            if self._decorator:
                self._update_decorator_terminal()
            else:
                if self._serial and self._stdin_chunks:
                    chunks, self._stdin_chunks = self._stdin_chunks, []
                    stdin_bytes = b"".join(chunk_to_bytes(c) for c in chunks)
                    self._serial.write(stdin_bytes)
                if self._serial_chunks:
                    chunks, self._serial_chunks = self._serial_chunks, []
                    serial_bytes = b"".join(chunk_to_bytes(c) for c in chunks)
                    sys.stdout.buffer.write(serial_bytes)
                    sys.stdout.flush()

    def _update_decorator_terminal(self) -> None:
        # use the decorator for "fancy" terminal output
        assert self._decorator
        decor = self._decorator
        stdin_chunks, self._stdin_chunks = self._stdin_chunks, []
        serial_chunks, self._serial_chunks = self._serial_chunks, []

        decor.add_from_terminal.extend(stdin_chunks)
        decor.add_base.extend(serial_chunks)
        decor.update(timestamp := time.monotonic())

        from_term, decor.out_from_terminal = decor.out_from_terminal, []
        for chunk in from_term:
            # TODO: check for menu keys, etc.
            if self._serial:
                self._serial.write(chunk_to_bytes(chunk))

        decor.update(timestamp)  # pick up output from input

        to_term, decor.out_to_terminal = decor.out_to_terminal, []
        sys.stdout.buffer.write(b"".join(chunk_to_bytes(c) for c in to_term))
        sys.stdout.flush()

    async def _read_from_stdin(self) -> None:
        async with _async_reader_context(sys.stdin) as inp:
            chunker = TerminalChunker()
            while True:
                try:
                    timeout = from_deadline(chunker.data_deadline)
                    async with asyncio.timeout(timeout):
                        if not (data := await inp.read(256)):
                            raise EOFError("Input closed")
                        chunker.add_data(data, time.monotonic())
                except TimeoutError:
                    chunker.add_data(b"", time.monotonic())
                if chunker.chunks:
                    self._stdin_chunks.extend(chunker.chunks)
                    self._new_data_event.set()
                    chunker.chunks.clear()

    async def _run_serial_monitor(self) -> None:
        with ok_serial.SerialConnectionMonitor(
            self._opts.match, copts=self._opts.copts, mopts=self._opts.mopts
        ) as monitor:
            while True:
                self._serial = await monitor.connect_async()
                name = self._serial.port_name
                msg = f"Connected to {name} ({self._opts.copts.baud}bps)"
                self._add_decor(b"\x1b[1;37;42m", f"► {msg}", b"\x1b[K")
                self._new_data_event.set()
                try:
                    await self._read_from_serial()
                except ok_serial.SerialIoException as ex:
                    msg = f"Connection lost: {ex}"
                    self._add_decor(b"\x1b[1;37;41m", f"► {msg}", b"\x1b[K")
                    self._serial = None
                    self._serial_signals = None
                    self._new_data_event.set()

    async def _read_from_serial(self) -> None:
        assert self._serial
        chunker = TerminalChunker()
        while True:
            try:
                # cap timeout to 0.2s for control signal polling
                timeout = min(0.2, from_deadline(chunker.data_deadline))
                async with asyncio.timeout(timeout):
                    data = await self._serial.read_async()
                    chunker.add_data(data, data.monotonic_time)
            except TimeoutError:
                chunker.add_data(b"", time.monotonic())

            try:
                signals = self._serial.get_signals()
            except ok_serial.SerialIoUnsupported:
                pass  # could be a pty
            else:
                if signals != self._serial_signals:
                    self._serial_signals = signals
                    self._new_data_event.set()

            if chunker.chunks:
                self._serial_chunks.extend(chunker.chunks)
                self._new_data_event.set()
                chunker.chunks.clear()

    def _capture_stderr(self, data: str) -> None:
        async def in_loop() -> None:
            self._stderr_buffer, buffer = "", self._stderr_buffer + data
            for line in buffer.splitlines(keepends=True):
                if not line.endswith(("\n", "\r")):
                    self._stderr_buffer += line
                elif self._decorator:
                    fancy: list[bytes | str]
                    fancy = [b"\x1b[47;30m", "▸ ", line.rstrip(), b"\x1b[K"]
                    self._decorator.add_above.append(fancy)
            self._new_data_event.set()

        asyncio.run_coroutine_threadsafe(in_loop(), self._event_loop)

    def _add_decor(self, *chunks: bytes | str) -> None:
        async def in_loop():
            if self._decorator:
                self._decorator.add_above.append(chunks)
            self._new_data_event.set()

        asyncio.run_coroutine_threadsafe(in_loop(), self._event_loop)

    def _shutdown_decorator(self) -> None:
        assert self._decorator
        try:
            self._decorator.shutdown()
            for chunk in self._decorator.out_to_terminal:
                sys.stdout.buffer.write(chunk_to_bytes(chunk))
            sys.stdout.flush()
        except OSError:
            pass  # ignore output write errors in shutdown


@contextlib.asynccontextmanager
async def _async_reader_context(
    stream: typing.IO,
) -> typing.AsyncIterator[asyncio.StreamReader]:
    reader = asyncio.StreamReader()
    loop = asyncio.get_running_loop()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.connect_read_pipe(lambda: protocol, stream)
    try:
        yield reader
    finally:
        transport.close()


@contextlib.contextmanager
def _raw_tty_context(fd: typing.Literal[0, 1, 2]) -> typing.Iterator[bool]:
    try:
        old_attr = termios.tcgetattr(fd)
    except termios.error:
        logging.debug("FD %d is not a terminal, skipping raw mode", fd)
        yield False  # not a tty
        return

    if fd == 0:
        raw_cc = [int(i == termios.VMIN) for i in range(len(old_attr[6]))]
        raw_attr = [0, old_attr[1], 0, 0, *old_attr[4:6], raw_cc]
    else:
        raw_attr = [old_attr[0], 0, *old_attr[2:]]

    logging.debug("Setting tty fd=%d to raw mode", fd)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, raw_attr)
        yield True  # is a tty
    finally:
        logging.debug("Restoring tty fd=%d to original mode", fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attr)


@contextlib.contextmanager
def _monkeypatch_context(obj: object, attr: str, val) -> typing.Iterator:
    save = getattr(obj, attr, None)
    try:
        setattr(obj, attr, val)
        yield save
    finally:
        setattr(obj, attr, save)
