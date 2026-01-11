import asyncio
import contextlib
import dataclasses
import logging
import threading
import time
import typing

from ok_serial._connection import SerialConnection, SerialConnectionOptions
from ok_serial._exceptions import (
    SerialIoClosed,
    SerialIoException,
    SerialOpenException,
)
from ok_serial._matcher import SerialPortMatcher
from ok_serial._scanning import SerialPort, scan_serial_ports
from ok_serial._timeout_math import from_deadline, to_deadline

log = logging.getLogger("ok_serial.tracker")


class TrackerOptions(typing.NamedTuple):
    scan_interval: float | int = 0.5


class SerialPortTracker(contextlib.AbstractContextManager):
    def __init__(
        self,
        match: str | SerialPortMatcher,
        *,
        baud: int = 0,
        topts: TrackerOptions = TrackerOptions(),
        copts: SerialConnectionOptions = SerialConnectionOptions(),
    ):
        if isinstance(match, str):
            match = SerialPortMatcher(match)
        if baud:
            copts = dataclasses.replace(copts, baud=baud)

        self._match = match
        self._tracker_opts = topts
        self._conn_opts = copts

        self._lock = threading.Lock()
        self._scan_results: list[SerialPort] = []
        self._next_scan = 0.0
        self._conn: SerialConnection | None = None

        log.debug("Tracking: %r%s", str(match), "" if match else " (any port)")

    def __exit__(self, exc_type, exc_value, traceback):
        with self._lock:
            if self._conn:
                self._conn.close()

    def __repr__(self) -> str:
        return (
            f"SerialPortTracker({self._match!r}, "
            f"topts={self._tracker_opts!r}, "
            f"copts={self._conn_opts!r})"
        )

    def find_sync(self, timeout: float | int | None = None) -> list[SerialPort]:
        deadline = to_deadline(timeout)
        while True:
            with self._lock:
                if (wait := from_deadline(self._next_scan)) <= 0:
                    wait = self._tracker_opts.scan_interval
                    self._next_scan = to_deadline(wait)

                    found = scan_serial_ports()
                    matched = [p for p in found if self._match.matches(p)]
                    self._scan_results = matched

                    nf, nm = len(found), len(matched)
                    log.debug("%d/%d ports match %r", nm, nf, str(self._match))

                if self._scan_results:
                    return self._scan_results

            timeout_wait = from_deadline(deadline)
            if timeout_wait < wait:
                return []

            log.debug("Next scan in %.2fs", wait)
            time.sleep(wait)

    async def find_async(self) -> list[SerialPort]:
        while True:
            with self._lock:
                next_scan = self._next_scan
            if ports := self.find_sync(timeout=0):
                return ports
            wait = from_deadline(next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)

    def connect_sync(
        self, timeout: float | int | None = None
    ) -> SerialConnection | None:
        deadline = to_deadline(timeout)
        while True:
            with self._lock:
                if self._conn:
                    try:
                        self._conn.write(b"")  # check for liveness
                        return self._conn
                    except SerialIoClosed:
                        log.debug("%s closed", self._conn.port_name)
                        self._conn = None
                    except SerialIoException as exc:
                        name = self._conn.port_name
                        log.warning("%s failed (%s)", name, exc)
                        self._conn.close()
                        self._conn = None

                for port in self._scan_results:
                    try:
                        self._conn = SerialConnection(
                            port=port, opts=self._conn_opts
                        )
                        return self._conn
                    except SerialOpenException as exc:
                        log.warning("Can't open %s (%s)", port, exc)

            if not self.find_sync(timeout=from_deadline(deadline)):
                return None

    async def connect_async(self) -> SerialConnection:
        while True:
            with self._lock:
                next_scan = self._next_scan
            if conn := self.connect_sync(timeout=0):
                return conn
            wait = from_deadline(next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)
