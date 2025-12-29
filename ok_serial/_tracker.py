import asyncio
import contextlib
import dataclasses
import logging
import threading
import time
import typing

from ok_serial import _connection
from ok_serial import _exceptions
from ok_serial import _matcher
from ok_serial import _scanning
from ok_serial import _timeout_math

log = logging.getLogger("ok_serial.tracker")


class TrackerOptions(typing.NamedTuple):
    scan_interval: float | int = 0.5


class SerialTracker(contextlib.AbstractContextManager):
    def __init__(
        self,
        match: str | _matcher.SerialPortMatcher | None = None,
        *,
        port: str | _scanning.SerialPort | None = None,
        baud: int = 0,
        topts: TrackerOptions = TrackerOptions(),
        copts: _connection.SerialOptions = _connection.SerialOptions(),
    ):
        assert bool(match) + bool(port) == 1, "Need one of match= or port="
        if isinstance(match, str):
            match = _matcher.SerialPortMatcher(match)
        if isinstance(port, str):
            port = _scanning.SerialPort(name=port, attr={"device": port})
        if baud:
            copts = dataclasses.replace(copts, baud=baud)

        self._match = match
        self._port = port
        self._tracker_opts = topts
        self._conn_opts = copts

        self._lock = threading.Lock()
        self._found_ports: list[_scanning.SerialPort] = []
        self._next_scan = 0.0
        self._conn: _connection.SerialConnection | None = None

        log.debug("Tracking %s %s", match, topts)

    def __exit__(self, exc_type, exc_value, traceback):
        with self._lock:
            if self._conn:
                self._conn.close()

    def __repr__(self) -> str:
        return f"SerialTracker({self._tracker_opts!r}, {self._conn_opts!r})"

    def find_sync(
        self, timeout: float | int | None = None
    ) -> list[_scanning.SerialPort]:
        if self._port:
            return [self._port]

        assert self._match
        deadline = _timeout_math.to_deadline(timeout)
        while True:
            with self._lock:
                if (wait := _timeout_math.from_deadline(self._next_scan)) <= 0:
                    wait = self._tracker_opts.scan_interval
                    self._next_scan = _timeout_math.to_deadline(wait)
                    self._found_ports = [
                        found
                        for found in _scanning.scan_serial_ports()
                        if self._match.matches(found)
                    ]

                if self._found_ports:
                    return self._found_ports

            timeout_wait = _timeout_math.from_deadline(deadline)
            if timeout_wait < wait:
                return []

            log.debug("Next scan in %.2fs", wait)
            time.sleep(wait)

    async def find_async(self) -> list[_scanning.SerialPort]:
        while True:
            with self._lock:
                next_scan = self._next_scan
            if ports := self.find_sync(timeout=0):
                return ports
            wait = _timeout_math.from_deadline(next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)

    def connect_sync(
        self, timeout: float | int | None = None
    ) -> _connection.SerialConnection | None:
        deadline = _timeout_math.to_deadline(timeout)
        while True:
            with self._lock:
                if self._conn:
                    try:
                        self._conn.write(b"")  # check for liveness
                        return self._conn
                    except _exceptions.SerialIoClosed:
                        log.debug("%s closed", self._conn.port_name)
                        self._conn = None
                    except _exceptions.SerialIoException as exc:
                        name = self._conn.port_name
                        log.warning("%s failed (%s)", name, exc)
                        self._conn.close()
                        self._conn = None

                for port in self._found_ports:
                    try:
                        self._conn = _connection.SerialConnection(
                            port=port, opts=self._conn_opts
                        )
                        return self._conn
                    except _exceptions.SerialOpenException as exc:
                        log.warning("Can't open %s (%s)", port, exc)

            find_timeout = _timeout_math.from_deadline(deadline)
            if not self.find_sync(timeout=find_timeout):
                return None

    async def connect_async(self) -> _connection.SerialConnection:
        while True:
            with self._lock:
                next_scan = self._next_scan
            if conn := self.connect_sync(timeout=0):
                return conn
            wait = _timeout_math.from_deadline(next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)
