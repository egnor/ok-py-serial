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

from ok_serial._terminal_chunker import TerminalChunker
from ok_serial._timeout_math import from_deadline


@dataclasses.dataclass(frozen=True)
class SerialTerminalOptions:
    match: str
    topts: ok_serial.SerialTrackerOptions
    copts: ok_serial.SerialConnectionOptions
    raw: bool = False


def run_terminal(opts: SerialTerminalOptions):
    """Synchronous wrapper for `run_terminal_async`"""
    asyncio.run(run_terminal_async(opts))


async def run_terminal_async(opts: SerialTerminalOptions):
    """Runs an interactive terminal communicating with a serial tracker"""
    await _TerminalSession().run(opts)


class _TerminalSession:
    async def run(self, opts: SerialTerminalOptions) -> None:
        async with contextlib.AsyncExitStack() as cleanup:
            self._event_loop = asyncio.get_running_loop()
            self._new_data_event = asyncio.Event()
            self._serial: ok_serial.SerialConnection | None = None
            self._from_stdin: list[bytes | str] = []
            self._from_serial: list[bytes | str] = []
            self._stderr_capture: list[str] = []

            # Inject stderr shim before putting tty in raw mode
            if os.isatty(2) and os.stat(1) == os.stat(2):
                stderr_patch_args = (sys.stderr, "write", self._capture_stderr)
                stderr_patch_context = _monkeypatch_context(*stderr_patch_args)
                cleanup.enter_context(stderr_patch_context)

            self._stdin_is_tty = cleanup.enter_context(_raw_tty_context(0))
            self._stdout_is_tty = cleanup.enter_context(_raw_tty_context(1))

            task_group = await cleanup.enter_async_context(asyncio.TaskGroup())
            task_group.create_task(self._read_from_stdin())
            task_group.create_task(self._run_serial_tracker(opts))
            await self._main_loop()

    async def _main_loop(self) -> None:
        echo_deadline: float | None = None
        while True:
            try:
                async with asyncio.timeout(from_deadline(echo_deadline)):
                    await self._new_data_event.wait()
            except TimeoutError:
                pass

            self._new_data_event.clear()
            from_stdin, self._from_stdin = self._from_stdin, []
            from_serial, self._from_serial = self._from_serial, []
            stderr_capture, self._stderr_capture = self._stderr_capture, []

            for chunk in from_stdin:
                self._on_stdin_chunk(chunk)

            for chunk in from_serial:
                self._on_serial_chunk(chunk)

            for chunk in stderr_capture:
                self._on_stderr_capture(chunk)

    def _on_stdin_chunk(self, chunk: bytes | str):
        # TODO: look for menu escape key
        # TODO: if in menu mode, interpret menu commands
        if self._serial:
            chunk_bytes = chunk if isinstance(chunk, bytes) else chunk.encode()
            self._serial.write(chunk_bytes)
        else:
            # TODO: beep, show text indicating connection closed?
            pass

    def _on_serial_chunk(self, chunk: bytes | str):
        # TODO: clean up pending typeahead / menu
        # TODO: restore terminal context, move to newline if not
        if isinstance(chunk, str):
            sys.stdout.write(chunk)
        else:
            # TODO: handle various escape codes, incl. newline variants
            sys.stdout.buffer.write(chunk)

        sys.stdout.flush()

    def _on_stderr_capture(self, chunk: str):
        sys.stdout.write(chunk.replace("\n", "\r\n"))

    async def _read_from_stdin(self):
        async with _async_reader_context(sys.stdin) as inp:
            chunker = TerminalChunker()
            while True:
                try:
                    async with asyncio.timeout(from_deadline(chunker.deadline)):
                        chunker.add_data(await inp.read(256), time.monotonic())
                except TimeoutError:
                    chunker.add_data(b"", time.monotonic())
                if chunks := chunker.get_chunks():
                    self._from_stdin.extend(chunks)
                    self._new_data_event.set()

    async def _run_serial_tracker(self, opts: SerialTerminalOptions):
        SPT = ok_serial.SerialPortTracker
        with SPT(opts.match, topts=opts.topts, copts=opts.copts) as tracker:
            while True:
                self._serial = await tracker.connect_async()
                self._new_data_event.set()
                try:
                    await self._read_from_serial()
                except ok_serial.SerialIoException as ex:
                    logging.warning("%s", ex)
                    self._serial = None
                    self._new_data_event.set()

    async def _read_from_serial(self):
        chunker = TerminalChunker()
        while True:
            try:
                async with asyncio.timeout(from_deadline(chunker.deadline)):
                    data = await self._serial.read_async()
                    chunker.add_data(data, data.monotonic_time)
            except TimeoutError:
                chunker.add_data(b"", time.monotonic())

            if chunks := chunker.get_chunks():
                self._from_serial.extend(chunks)
                self._new_data_event.set()

    def _capture_stderr(self, data: str):
        async def in_loop(data: str):
            self._stderr_capture.append(data)
            self._new_data_event.set()

        asyncio.run_coroutine_threadsafe(in_loop(data), self._event_loop)


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
