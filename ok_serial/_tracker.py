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

    def get_connection_sync(
        self, timeout: float | None = None
    ) -> _connection.SerialConnection | None:
        deadline = _timeout_math.to_deadline(timeout)
        while True:
            with self._conn_lock:
                if self._conn:
                    try:
                        self._conn.write(b"")
                        return self._conn
                    except _exceptions.SerialIoException:
                        log.warning("Connection failed, will rescan")
                        self._conn.close()
                        self._conn = None
                        raise

                poll_wait = _timeout_math.from_deadline(self._next_scan)
                if poll_wait <= 0:
                    ports = _scanning.scan_serial_ports()
                    matcher = self._tracker_opts.matcher
                    matching = [p for p in ports if matcher.matches(p)]
                    np, nm = len(ports), len(matching)
                    log.warning("Scanned %d ports, %d match...", np, nm)
                    for attr in matching:
                        try:
                            port, opt = attr.port, self._conn_opts
                            self._conn = _connection.SerialConnection(port, opt)
                            log.debug(f"Opened {port}")
                            return self._conn
                        except _exceptions.SerialIoException:
                            log.warning("Can't open %s", exc_info=True)

                    interval = self._tracker_opts.scan_interval
                    self._next_scan = time.monotonic() + interval

            wait = min(poll_wait, _timeout_math.from_deadline(deadline))
            if wait <= 0:
                return None
            log.debug("Next scan in %.2fs", wait)
            time.sleep(wait)

    async def get_connection_async(self) -> _connection.SerialConnection:
        while not (conn := self.get_connection_sync(timeout=0)):
            with self._conn_lock:
                poll_wait = _timeout_math.from_deadline(self._next_scan)
            await asyncio.sleep(poll_wait)

        return conn
