import ok_serial

import asyncio
import contextlib
import dataclasses
import logging
import sys
import termios
import tty
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

    async with contextlib.AsyncExitStack() as exits:
        tracker = exits.enter_context(
            ok_serial.SerialPortTracker(
                opts.match, topts=opts.tracker, copts=opts.connection
            )
        )

        raw_stdin = exits.enter_context(raw_tty_context(sys.stdin))
        stdin_reader_context = stream_reader_async_context(raw_stdin)
        stdin_reader = await exits.enter_async_context(stdin_reader_context)

        session = _TerminalSession()
        session_tasks = await exits.enter_async_context(asyncio.TaskGroup())
        session_tasks.create_task(session.stdin_reader_task(stdin_reader))

        while True:
            logging.info("🔎 Scanning for ports matching %r", opts.match)
            conn = await tracker.connect_async()
            logging.info("✅ Connected to %s", conn.port_name)
            try:
                await session.serial_reader_task(conn)
            except ok_serial.SerialIoException as e:
                logging.error("%s", e)


class _TerminalSession:
    def __init__(self) -> None:
        self._conn: ok_serial.SerialConnection | None = None

    async def stdin_reader_task(self, stdin: asyncio.StreamReader):
        while True:
            data = await stdin.read(256)
            print("STDIN", data, end="\r\n")

    async def serial_reader_task(self, conn: ok_serial.SerialConnection):
        self._conn = conn
        try:
            chunker = TerminalChunker()
            while True:
                deadline = chunker.partial_deadline or TIMEOUT_MAX
                try:
                    async with asyncio.timeout(from_deadline(deadline)):
                        data = await conn.read_async()
                        chunker.add_data(data, data.monotonic_time)
                except TimeoutError:
                    chunker.add_data(b"", deadline)
                print("CHUNKS", chunker.read_chunks(), end="\r\n")
        finally:
            self._conn = None


@contextlib.contextmanager
def raw_tty_context(f: typing.IO) -> typing.Iterator[typing.IO]:
    try:
        saved_attr = termios.tcgetattr(f.fileno())
    except termios.error:
        logging.debug("Not a terminal, skipping raw mode")
        yield f
        return
    try:
        logging.debug("Setting terminal to raw mode")
        tty.setraw(f.fileno())
        yield f
    finally:
        logging.debug("Restoring terminal to original mode")
        termios.tcsetattr(f.fileno(), termios.TCSADRAIN, saved_attr)


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
