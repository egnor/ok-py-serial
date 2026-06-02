import ok_serial

import asyncio
import logging
import sys


async def run_terminal_async(tracker: ok_serial.SerialPortTracker):
    """Runs an interactive terminal communicating with the serial tracker."""

    session = _TerminalSession()

    while True:
        logging.info("🔎 Scanning for serial ports: %r", tracker.match)
        conn = await tracker.connect_async()
        logging.info("✅ Connected to %s", conn.port_name)
        await session.run_async(conn)


class _TerminalSession:
    def __init__(self):
        self._conn: ok_serial.SerialConnection | None = None
        self._stdin_reader = asyncio.StreamReader()
        self._stdin_transport: asyncio.Transport | None = None

        loop = asyncio.get_running_loop()
        stdin_protocol = asyncio.StreamReaderProtocol(self._stdin_reader)
        self._stdin_transport, _ = await loop.connect_read_pipe(
            lambda: stdin_protocol, sys.stdin
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._stdin_transport:
            self._stdin_transport.close()
            self._stdin_transport = None

    async def run_async(self, conn: ok_serial.SerialConnection):
        self._conn = conn

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._stdin_to_serial_task())
            tg.create_task(self._serial_to_stdout_task())

    async def _stdin_to_serial_task(self):
        pass

    async def _serial_to_stdout_task(self):
        pass
