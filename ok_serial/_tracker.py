import asyncio
import contextlib
import logging
import pydantic
import threading
import time

from ok_serial import _connection
from ok_serial import _exceptions
from ok_serial import _scanning
from ok_serial import _timeout_math

log = logging.getLogger("ok_serial.tracker")


class TrackerOptions(pydantic.BaseModel):
    matcher: _scanning.SerialPortMatcher
    scan_interval: float = 0.5


class SerialTracker(contextlib.AbstractContextManager):
    @pydantic.validate_call(config={"arbitrary_types_allowed": True})
    def __init__(
        self,
        topts: str | TrackerOptions,
        copts: int | _connection.SerialOptions = _connection.SerialOptions(),
    ):
        if isinstance(topts, str):
            topts = TrackerOptions(matcher=_scanning.SerialPortMatcher(topts))

        self._tracker_opts = topts
        self._conn_opts = copts
        self._conn_lock = threading.Lock()
        self._conn: _connection.SerialConnection | None = None
        self._next_scan = 0.0

        log.debug("Tracking [%s]", topts.matcher.spec)

    def __exit__(self, exc_type, exc_value, traceback):
        with self._conn_lock:
            if self._conn:
                self._conn.close()

    def __repr__(self) -> str:
        return f"SerialTracker({self._tracker_opts!r}, {self._conn_opts!r})"

    def connect_sync(
        self, timeout: float | None = None
    ) -> _connection.SerialConnection | None:
        deadline = _timeout_math.to_deadline(timeout)
        while True:
            with self._conn_lock:
                if self._conn:
                    try:
                        self._conn.write(b"")
                        return self._conn
                    except _exceptions.SerialIoClosed:
                        log.debug("%s closed, scanning", self._conn.port)
                        self._conn = None
                    except _exceptions.SerialIoException as exc:
                        msg, port = "%s failed, scanning (%s)", self._conn.port
                        log.warning(msg, port, exc)
                        self._conn.close()
                        self._conn = None

                poll_wait = _timeout_math.from_deadline(self._next_scan)
                if poll_wait <= 0:
                    ports = _scanning.scan_serial_ports()
                    matcher = self._tracker_opts.matcher
                    matching = [p for p in ports if matcher.matches(p)]
                    np, nm = len(ports), len(matching)
                    log.debug('%d/%d ports match "%s"', nm, np, matcher.spec)
                    for attr in matching:
                        port, opt = attr.port, self._conn_opts
                        try:
                            self._conn = _connection.SerialConnection(port, opt)
                            log.debug(f"Opened {port}")
                            return self._conn
                        except _exceptions.SerialOpenException as exc:
                            log.warning("Can't open %s (%s)", port, exc)

                    interval = self._tracker_opts.scan_interval
                    self._next_scan = time.monotonic() + interval

            wait = min(poll_wait, _timeout_math.from_deadline(deadline))
            if wait <= 0:
                return None
            log.debug("Next scan in %.2fs", wait)
            time.sleep(wait)

    async def connect_async(self) -> _connection.SerialConnection:
        while True:
            with self._conn_lock:
                next_scan = self._next_scan
            if conn := self.connect_sync(timeout=0):
                return conn
            wait = _timeout_math.from_deadline(next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)
