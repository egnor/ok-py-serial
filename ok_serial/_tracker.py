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
    match: _scanning.SerialPortMatcher
    poll_seconds: float = 0.5


class SerialTracker(contextlib.AbstractContextManager):
    @pydantic.validate_call(config={"arbitrary_types_allowed": True})
    def __init__(
        self,
        topts: str | TrackerOptions,
        copts: int | _connection.SerialOptions = _connection.SerialOptions(),
    ):
        if isinstance(topts, str):
            topts = TrackerOptions(match=_scanning.SerialPortMatcher(topts))

        self._tracker_opts = topts
        self._conn_opts = copts
        self._conn_lock = threading.Lock()
        self._conn: _connection.SerialConnection | None = None
        self._next_poll = 0.0

    def __exit__(self, exc_type, exc_value, traceback):
        with self._conn_lock:
            if self._conn:
                self._conn.close()

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
                        self._conn.close()
                        self._conn = None
                        raise

                poll_wait = _timeout_math.from_deadline(self._next_poll)
                if poll_wait <= 0:
                    for port in _scanning.scan_serial_ports():
                        if self._tracker_opts.matches(port):
                            try:
                                self._conn = _connection.SerialConnection(
                                    port, self._conn_opts
                                )
                                return self._conn
                            except _exceptions.SerialIoException as ex:
                                pass
                    continue

            wait = min(poll_wait, _timeout_math.from_deadline(deadline))
            if wait <= 0:
                return None
            time.sleep(wait)

    async def get_connection_async(self) -> _connection.SerialConnection:
        while not (conn := self.get_connection_sync(timeout=0)):
            with self._conn_lock:
                poll_wait = _timeout_math.from_deadline(self._next_poll)
            await asyncio.sleep(poll_wait)

        return conn
