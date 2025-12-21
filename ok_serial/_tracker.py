import asyncio
import contextlib
import dataclasses
import logging
import threading
import time
import typing

from ok_serial import _connection
from ok_serial import _exceptions
from ok_serial import _scanning
from ok_serial import _timeout_math

log = logging.getLogger("ok_serial.tracker")


class TrackerOptions(typing.NamedTuple):
    scan_interval: float | int = 0.5


class SerialTracker(contextlib.AbstractContextManager):
    def __init__(
        self,
        match: str | _scanning.SerialPortMatcher | None = None,
        *,
        port: str | _scanning.SerialPort | None = None,
        baud: int = 0,
        topts: TrackerOptions = TrackerOptions(),
        copts: _connection.SerialOptions = _connection.SerialOptions(),
    ):
        if isinstance(match, str):
            match = _scanning.SerialPortMatcher(match)
        if isinstance(port, _scanning.SerialPort):
            port = port.name
        if baud:
            copts = dataclasses.replace(copts, baud=baud)

        self._match = match
        self._port = port
        self._tracker_opts = topts
        self._conn_opts = copts
        self._conn_lock = threading.Lock()
        self._conn: _connection.SerialConnection | None = None
        self._next_scan = 0.0

        log.debug("Tracking %s %s", match, topts)

    def __exit__(self, exc_type, exc_value, traceback):
        with self._conn_lock:
            if self._conn:
                self._conn.close()

    def __repr__(self) -> str:
        return f"SerialTracker({self._tracker_opts!r}, {self._conn_opts!r})"

    def connect_sync(
        self, timeout: float | int | None = None
    ) -> _connection.SerialConnection | None:
        deadline = _timeout_math.to_deadline(timeout)
        while True:
            with self._conn_lock:
                if self._conn:
                    try:
                        self._conn.write(b"")  # check for liveness
                        return self._conn
                    except _exceptions.SerialIoClosed:
                        port = self._conn.port_name
                        log.debug("%s closed, scanning", port)
                        self._conn = None
                    except _exceptions.SerialIoException as exc:
                        port = self._conn.port_name
                        log.warning("%s failed, scanning (%s)", port, exc)
                        self._conn.close()
                        self._conn = None

                if _timeout_math.from_deadline(self._next_scan) <= 0:
                    if self._match:
                        scan = _scanning.scan_serial_ports(self._match)
                        ports = [p.name for p in scan]
                    else:
                        assert self._port
                        ports = [self._port]

                    for port in ports:
                        try:
                            self._conn = _connection.SerialConnection(
                                port=port, opts=self._conn_opts
                            )
                            return self._conn
                        except _exceptions.SerialOpenException as exc:
                            log.warning("Can't open %s (%s)", port, exc)

                    interval = self._tracker_opts.scan_interval
                    self._next_scan = time.monotonic() + interval

                poll_wait = _timeout_math.from_deadline(self._next_scan)
                log.debug("Next scan in %.2fs", poll_wait)

            timeout_wait = _timeout_math.from_deadline(deadline)
            if timeout_wait <= 0:
                return None

            time.sleep(min(poll_wait, timeout_wait))

    async def connect_async(self) -> _connection.SerialConnection:
        while True:
            with self._conn_lock:
                next_scan = self._next_scan
            if conn := self.connect_sync(timeout=0):
                return conn
            wait = _timeout_math.from_deadline(next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)
