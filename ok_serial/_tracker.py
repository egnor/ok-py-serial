import asyncio
import contextlib
import dataclasses
import logging
import threading
import time

from ok_serial._connection import SerialConnection, SerialConnectionOptions
from ok_serial._exceptions import (
    SerialException,
    SerialIoClosed,
    SerialIoException,
    SerialOpenException,
    SerialScanException,
    SerialTrackerExhausted,
)
from ok_serial._metadata import SerialPort, PortPredicate
from ok_serial._scanning import scan_serial_ports
from ok_serial._timeout_math import from_deadline, to_deadline

log = logging.getLogger("ok_serial.tracker")


@dataclasses.dataclass(frozen=True)
class SerialTrackerOptions:
    """Optional parameters for `SerialPortTracker`."""

    scan_interval: float | int = 0.5
    """Seconds between port re-scans when waiting for a match."""

    scan_timeout: float | int | None = None
    """Seconds to scan before giving up permanently (None = no limit)."""

    reconnect_limit: int | None = None
    """Reconnection attempts before giving up permanently (None = no limit)."""


class SerialPortTracker(contextlib.AbstractContextManager):
    """
    Utility class to maintain a connection to a serial port of interest,
    re-scanning and re-connecting as needed after errors, with periodic retry.
    This is used for robust communication with a serial device which might be
    plugged and unplugged during operation.
    """

    def __init__(
        self,
        match: str | PortPredicate | None = None,
        *,
        baud: int = 0,
        topts: SerialTrackerOptions = SerialTrackerOptions(),
        copts: SerialConnectionOptions = SerialConnectionOptions(),
    ):
        """
        Prepare to manage a serial port connection.
        - `match` selects the port of interest: a
          [match string](https://github.com/egnor/ok-py-serial#port-matching),
          a `SerialPort -> bool` callable, or `None` for any port
        - `topts` can define parameters for tracking (eg. re-scan interval)
        - `copts` can define parameters for connecting (eg. baud rate)
          - OR `baud` can set the baud rate (as a shortcut)

        Actual port scans and connections only happen when `connect_*`
        is called. Call `close` to end any open connection, and/or use
        `SerialPortTracker` as the target of a `with` statement.
        """

        if baud:
            copts = dataclasses.replace(copts, baud=baud)

        self.match = match
        self._tracker_opts = topts
        self._conn_opts = copts

        self._lock = threading.Lock()
        self._baseline_keys: set[str] | None = None
        self._scan_matched: SerialPort | None = None
        self._scan_deadline: float | None = None
        self._next_scan = 0.0
        self._reconnect_count = 0
        self._conn: SerialConnection | None = None
        self._conn_error: SerialException | None = None

        log.debug("Tracking %r", match or "(any port)")

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __repr__(self) -> str:
        return (
            f"SerialPortTracker({self.match!r}, "
            f"topts={self._tracker_opts!r}, "
            f"copts={self._conn_opts!r})"
        )

    def close(self) -> None:
        """
        Closes any open connection with `SerialConnection.close`. A subsequent
        call to `connect_sync`/`connect_async` will establish a new connection.
        """

        with self._lock:
            if self._conn:
                log.debug("Closing %s", self._conn.port_name)
                self._conn.close()

    def connect_sync(
        self, timeout: float | int | None = None
    ) -> SerialConnection | None:
        """
        If a connection is established and healthy, returns it immediately.

        Otherwise, waits up to `timeout` seconds (forever for `None`) for
        serial port(s) to appear matching this tracker's requirements,
        returning the first successful connection from among them.

        Returns `None` on reaching the timeout argument.

        Raises:
        - `SerialScanException` - System error scanning ports
        - `SerialTrackerExhausted` - Permanent timeout or reconnect limit hit
        """

        call_deadline = to_deadline(timeout)
        while True:
            with self._lock:
                # Return an existing live connection if possible
                if self._conn:
                    try:
                        self._conn.write(b"")  # check for liveness
                        return self._conn
                    except SerialIoException as ex:
                        if self._tracker_opts.reconnect_limit == 0:
                            msg = f"{ex} (reconnect disabled)"
                            raise SerialTrackerExhausted(msg) from ex
                        log_level = 20 if isinstance(ex, SerialIoClosed) else 30
                        log.log(log_level, "⛓️‍💥 %s", ex)

                    self._conn.close()
                    self._conn = None
                    self._reconnect_count += 1
                    limit = self._tracker_opts.reconnect_limit
                    if limit is not None and self._reconnect_count > limit:
                        msg = f"{self.match!r} reconnect limit met ({limit})"
                        raise SerialTrackerExhausted(msg)

                if self._scan_deadline is None:
                    scan_timeout = self._tracker_opts.scan_timeout
                    self._scan_deadline = to_deadline(scan_timeout)
                    if scan_timeout is None:
                        log.info("🔎 Scanning for %r (ongoing)", self.match)
                    elif scan_timeout > 0:
                        msg = "🔎 Scanning for %r (%.2fs timeout)"
                        log.info(msg, self.match, scan_timeout)
                    else:
                        log.info("🔎 Looking for %r", self.match)

                # Re-scan for ports at the specified interval
                if (wait := from_deadline(self._next_scan)) <= 0:
                    matched = scan_serial_ports(self.match)
                    if len(matched) == 1:
                        self._scan_matched = matched[0]
                    elif matched:
                        detail = "".join(f"\n  {p}" for p in matched)
                        msg = f"Multiple ports match {self.match!r}:{detail}"
                        self._conn_error = SerialScanException(msg)
                        self._scan_matched = None
                        log.warning("%s", self._conn_error)
                    else:
                        msg = f"No ports match {self.match!r}"
                        self._conn_error = SerialScanException(msg)
                        self._scan_matched = None
                        log.debug("%s", self._conn_error)

                    wait = self._tracker_opts.scan_interval
                    self._next_scan = to_deadline(wait)

                if port := self._scan_matched:
                    try:
                        log.info("🔗 Connecting to %s", port.name)
                        opts = self._conn_opts
                        self._conn = SerialConnection(port=port, opts=opts)
                        self._conn_error = None
                        self._scan_deadline = None  # reset for next scan
                        return self._conn
                    except SerialOpenException as ex:
                        self._conn_error = ex
                        self._scan_matched = None  # cool down until re-scan
                        log.warning("%s", self._conn_error)

            assert self._scan_deadline is not None
            if from_deadline(self._scan_deadline) < wait:
                scan_timeout = self._tracker_opts.scan_timeout
                msg = f"Can't open {self.match!r}"
                if scan_timeout and scan_timeout > 0:
                    msg += f" ({scan_timeout:.2f}s timeout)"
                raise SerialTrackerExhausted(msg) from self._conn_error

            if from_deadline(call_deadline) < wait:
                return None

            log.debug("Next scan in %.2fs", wait)
            time.sleep(wait)

    async def connect_async(self) -> SerialConnection:
        """
        Similar to `connect_sync` but returns a coroutine instead of
        blocking the current thread.
        """

        while True:
            if conn := self.connect_sync(timeout=0):
                return conn
            with self._lock:
                wait = from_deadline(self._next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)
