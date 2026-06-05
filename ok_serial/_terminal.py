import ok_serial

import asyncio
import contextlib
import dataclasses
import logging
import sys
import termios
import time
import typing

from ok_serial._terminal_chunker import TerminalChunker
from ok_serial._timeout_math import from_deadline, TIMEOUT_MAX


@dataclasses.dataclass(frozen=True)
class SerialTerminalOptions:
    match: str
    tracker: ok_serial.SerialTrackerOptions
    connection: ok_serial.SerialConnectionOptions


def run_terminal(opts: SerialTerminalOptions):
    """Synchronous wrapper for `run_terminal_async`"""
    asyncio.run(run_terminal_async(opts))


async def run_terminal_async(opts: SerialTerminalOptions):
    """Runs an interactive terminal communicating with a serial tracker"""
    await _TerminalSession().run(opts)


class _TerminalSession:
    async def run(self, opts: SerialTerminalOptions) -> None:
        async with contextlib.AsyncExitStack() as exits:
            self._serial: ok_serial.SerialConnection | None = None

            self._stdin_is_tty = exits.enter_context(raw_tty_context(0))
            self._stdout_is_tty = exits.enter_context(raw_tty_context(1))

            stdin_reader_context = stream_reader_async_context(sys.stdin)
            stdin_reader = await exits.enter_async_context(stdin_reader_context)
            stdin_tasks = await exits.enter_async_context(asyncio.TaskGroup())
            stdin_tasks.create_task(self._stdin_reader_task(stdin_reader))

            tracker = exits.enter_context(
                ok_serial.SerialPortTracker(
                    opts.match, topts=opts.tracker, copts=opts.connection
                )
            )

            while True:
                logging.info("🔎 Scanning for ports matching %r", opts.match)
                self._serial = await tracker.connect_async()
                logging.info("✅ Connected to %s", self._serial.port_name)
                try:
                    await self._serial_reader_task()
                except ok_serial.SerialIoException as e:
                    logging.error("%s", e)  # loop, try again
                    self._serial.close()
                    self._serial = None

    async def _stdin_reader_task(self, stdin: asyncio.StreamReader):
        chunker = TerminalChunker()
        while True:
            try:
                async with asyncio.timeout(from_deadline(chunker.deadline)):
                    chunker.add_data(await stdin.read(256), time.monotonic())
            except TimeoutError:
                chunker.add_data(b"", time.monotonic())

            print("STDIN CHUNKS ", chunker.get_chunks(), end="\r\n")

    async def _serial_reader_task(self):
        chunker = TerminalChunker()
        while True:
            try:
                async with asyncio.timeout(from_deadline(chunker.deadline)):
                    data = await self._serial.read_async()
                    chunker.add_data(data, data.monotonic_time)
            except TimeoutError:
                chunker.add_data(b"", time.monotonic())

            print("SERIAL CHUNKS", chunker.get_chunks(), end="\r\n")


@contextlib.contextmanager
def raw_tty_context(fd: typing.Literal[0, 1]) -> typing.Iterator[bool]:
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

    logging.debug("Setting fd %d to raw mode", fd)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, raw_attr)
        yield True  # is a tty
    finally:
        logging.debug("Restoring fd %d to original mode", fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attr)


@contextlib.asynccontextmanager
async def stream_reader_async_context(
    f: typing.IO,
) -> typing.AsyncIterator[asyncio.StreamReader]:
    reader = asyncio.StreamReader()
    loop = asyncio.get_running_loop()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.connect_read_pipe(lambda: protocol, f)
    try:
        yield reader
    finally:
        transport.close()
