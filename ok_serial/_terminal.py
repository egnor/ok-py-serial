import ok_serial

import asyncio
import logging
import sys


async def run_terminal_async(tracker: ok_serial.SerialPortTracker):
    """Runs an interactive terminal communicating with the serial tracker."""

    async with _TerminalSession() as session:
        while True:
            logging.info("🔎 Scanning for serial ports: %r", tracker.match)
            conn = await tracker.connect_async()
            logging.info("✅ Connected to %s", conn.port_name)
            await session.run_async(conn)


class _TerminalSession:
    def __init__(self) -> None:
        self._conn: ok_serial.SerialConnection | None = None
        self._stdin_reader = asyncio.StreamReader()
        self._stdin_transport: asyncio.ReadTransport | None = None

    async def __aenter__(self) -> "_TerminalSession":
        assert self._stdin_transport is None
        loop = asyncio.get_running_loop()
        self._stdin_transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(self._stdin_reader), sys.stdin
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        assert self._stdin_transport is not None
        if self._stdin_transport:
            self._stdin_transport.close()
            self._stdin_transport = None

    async def run_async(self, conn: ok_serial.SerialConnection) -> None:
        self._conn = conn

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._stdin_to_serial_task())
            tg.create_task(self._serial_to_stdout_task())

    async def _stdin_to_serial_task(self) -> None:
        pass

    async def _serial_to_stdout_task(self) -> None:
        pass
