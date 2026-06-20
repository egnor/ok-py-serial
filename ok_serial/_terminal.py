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
            self._serial: ok_serial.SerialConnection | None = None
            self._serial_enable = asyncio.Event()
            self._serial_enable.set()

            # Inject stderr shim before putting tty in raw mode
            if os.isatty(2) and os.stat(1) == os.stat(2):
                stderr_patch_args = (sys.stderr, "write", self._on_stderr_chunk)
                stderr_patch_context = _setattr_context(*stderr_patch_args)
                cleanup.enter_context(stderr_patch_context)

            self._event_loop = asyncio.get_running_loop()
            self._echo_timer = self._event_loop.call_soon(lambda: None)
            cleanup.callback(lambda: self._echo_timer.cancel())

            self._stdin_is_tty = cleanup.enter_context(_raw_tty_context(0))
            self._stdout_is_tty = cleanup.enter_context(_raw_tty_context(1))

            stdin_context = _async_reader_context(sys.stdin)
            stdin_reader = await cleanup.enter_async_context(stdin_context)
            stdin_tasks = await cleanup.enter_async_context(asyncio.TaskGroup())
            stdin_tasks.create_task(self._run_stdin_reader(stdin_reader))

            SPT = ok_serial.SerialPortTracker
            tracker = SPT(opts.match, topts=opts.topts, copts=opts.copts)
            cleanup.enter_context(tracker)

            while True:
                self._serial = await tracker.connect_async()
                try:
                    await self._run_serial_reader()
                except ok_serial.SerialIoException:
                    self._serial = None  # loop and try again

    def _on_stdin_chunk(self, chunk: bytes | str):
        # TODO: look for menu escape key
        # TODO: if in menu mode, interpret menu commands
        if self._serial:
            # TODO: set echo timer
            if isinstance(chunk, bytes):
                self._serial.write(chunk)
            else:
                self._serial.write(chunk.encode())
        else:
            # TODO: beep, show text indicating connection closed?
            pass

    def _on_serial_chunk(self, chunk: bytes | str):
        self._echo_timer.cancel()
        # TODO: clean up pending typeahead / menu
        # TODO: restore termianl context, move to newline if not
        if isinstance(chunk, str):
            sys.stdout.write(chunk)
        else:
            # TODO: handle various escape codes, incl. newline variants
            sys.stdout.buffer.write(chunk)

        sys.stdout.flush()

    def _on_stderr_chunk(self, chunk: str):
        try:
            assert asyncio.get_running_loop() == self._event_loop
        except (AssertionError, RuntimeError):
            return sys.stdout.write(chunk.replace("\n", "\r\n"))  # pass through

        # TODO: clean up pending typeahead / menu
        # TODO: switch to error context, move to newline if not
        sys.stdout.write(chunk.replace("\n", "\r\n"))
        sys.stdout.flush()
        # TODO: replace pending typeahead / menu

    async def _run_stdin_reader(self, stdin: asyncio.StreamReader):
        assert asyncio.get_running_loop() == self._event_loop
        chunker = TerminalChunker()
        while True:
            try:
                async with asyncio.timeout(from_deadline(chunker.deadline)):
                    chunker.add_data(await stdin.read(256), time.monotonic())
            except TimeoutError:
                chunker.add_data(b"", time.monotonic())
            for chunk in chunker.get_chunks():
                self._on_stdin_chunk(chunk)

    async def _run_serial_reader(self):
        assert asyncio.get_running_loop() == self._event_loop
        chunker = TerminalChunker()
        while True:
            try:
                async with asyncio.timeout(from_deadline(chunker.deadline)):
                    data = await self._serial.read_async()
                    chunker.add_data(data, data.monotonic_time)
            except TimeoutError:
                chunker.add_data(b"", time.monotonic())

            for chunk in chunker.get_chunks():
                await self._serial_enable.wait()
                self._on_serial_chunk(chunk)


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
def _setattr_context(obj: object, attr: str, val) -> typing.Iterator:
    save = getattr(obj, attr, None)
    try:
        setattr(obj, attr, val)
        yield save
    finally:
        setattr(obj, attr, save)
