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
    SerialMonitorExhausted,
    SerialOpenException,
    SerialScanException,
)
from ok_serial._port import SerialPort, PortPredicate
from ok_serial._scan import scan_serial_ports
from ok_serial._timeout_math import from_deadline, to_deadline

log = logging.getLogger("ok_serial.monitor")


@dataclasses.dataclass(frozen=True)
class SerialMonitorOptions:
    """Optional parameters for `SerialConnectionMonitor`."""

    scan_interval: float | int = 0.5
    """Seconds between port re-scans when waiting for a match."""

    scan_timeout: float | int | None = None
    """Seconds to scan per (re)connection before giving up (None = no limit)."""


class SerialConnectionMonitor(contextlib.AbstractContextManager):
    """
    Utility class to maintain a connection to a serial port of interest,
    re-scanning and re-connecting as needed after errors, with periodic retry.
    This is used for robust communication with a serial device which might be
    plugged and unplugged during operation.

    Thread-safe: any method may be called from any thread any time, and any
    `*_async` method may be awaited from any event loop in any thread any time.
    """

    def __init__(
        self,
        match: str | PortPredicate | None = None,
        *,
        baud: int = 0,
        copts: SerialConnectionOptions = SerialConnectionOptions(),
        mopts: SerialMonitorOptions = SerialMonitorOptions(),
    ):
        """
        Prepare to manage a serial port connection.
        - `match` selects the port of interest: a
          [match string](https://github.com/egnor/ok-py-serial#port-matching),
          a `SerialPort -> bool` callable, or `None` for any port
        - `copts` can define parameters for connecting (eg. baud rate)
          - OR `baud` can set the baud rate (as a shortcut)
        - `mopts` can define parameters for tracking (eg. re-scan interval)

        Actual port scans and connections only happen when `connect_*`
        is called. Use `SerialConnectionMonitor` as the target of a `with`
        statement to release any open connection when you're done with it.
        (After that, don't use it again, create a fresh one to resume.)
        """

        if baud:
            copts = dataclasses.replace(copts, baud=baud)

        self._match = match
        self._copts = copts
        self._mopts = mopts

        self._lock = threading.Lock()
        self._scan_matched: SerialPort | None = None
        self._scan_deadline: float | None = None
        self._next_scan = 0.0
        self._conn: SerialConnection | None = None
        self._conn_error: SerialException | None = None

        log.debug("Tracking %r", match or "(any port)")

    def __exit__(self, exc_type, exc_value, traceback):
        """Closes any open connection with `SerialConnection.close`."""

        with self._lock:
            if self._conn:
                log.debug("Closing %s", self._conn.port_name)
                self._conn.close()
                self._conn = None

    def __repr__(self) -> str:
        return (
            f"SerialConnectionMonitor({self._match!r}, "
            f"copts={self._copts!r}), "
            f"mopts={self._mopts!r}"
        )

    def connect_sync(
        self, timeout: float | int | None = None
    ) -> SerialConnection | None:
        """
        If a connection is established and healthy, returns it immediately.

        Otherwise, waits up to `timeout` seconds (forever for `None`) for
        serial port(s) to appear matching this monitor's requirements,
        returning the first successful connection from among them.

        Returns `None` on reaching the timeout argument.

        Raises:
        - `SerialScanException` - System error scanning ports
        - `SerialMonitorExhausted` - Gave up scanning (see `scan_timeout`)
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
                        log_level = 20 if isinstance(ex, SerialIoClosed) else 30
                        log.log(log_level, "⛓️‍💥 %s (reconnecting)", ex)

                    self._conn.close()
                    self._conn = None

                if self._scan_deadline is None:
                    scan_timeout = self._mopts.scan_timeout
                    self._scan_deadline = to_deadline(scan_timeout)
                    if scan_timeout is None:
                        log.info("🔎 Scanning for %r (ongoing)", self._match)
                    elif scan_timeout > 0:
                        msg = "🔎 Scanning for %r (%.2fs timeout)"
                        log.info(msg, self._match, scan_timeout)
                    else:
                        log.info("🔎 Looking for %r", self._match)

                # Re-scan for ports at the specified interval
                if (wait := from_deadline(self._next_scan)) <= 0:
                    matched = scan_serial_ports(self._match)
                    if len(matched) == 1:
                        self._scan_matched = matched[0]
                    elif matched:
                        detail = "".join(f"\n  {p}" for p in matched)
                        msg = f"Multiple ports match {self._match!r}:{detail}"
                        self._conn_error = SerialScanException(msg)
                        self._scan_matched = None
                        log.warning("%s", self._conn_error)
                    else:
                        msg = f"No ports match {self._match!r}"
                        self._conn_error = SerialScanException(msg)
                        self._scan_matched = None
                        log.debug("%s", self._conn_error)

                    wait = self._mopts.scan_interval
                    self._next_scan = to_deadline(wait)

                if port := self._scan_matched:
                    try:
                        msg = "🔌 Connecting to %s (%dbps)"
                        log.info(msg, port.name, self._copts.baud)
                        conn = SerialConnection(port=port, opts=self._copts)
                        self._conn, self._conn_error = conn, None
                        self._scan_deadline = None  # reset for next scan
                        return self._conn
                    except SerialOpenException as ex:
                        self._conn_error = ex
                        self._scan_matched = None  # cool down until re-scan
                        log.warning("%s", self._conn_error)

                assert self._scan_deadline is not None  # still under the lock
                if from_deadline(self._scan_deadline) < wait:
                    scan_timeout = self._mopts.scan_timeout
                    msg = f"Can't open {self._match!r}"
                    if scan_timeout and scan_timeout > 0:
                        msg += f" ({scan_timeout:.2f}s timeout)"
                    raise SerialMonitorExhausted(msg) from self._conn_error

            if from_deadline(call_deadline) < wait:
                return None

            log.debug("Next scan in %.2fs", wait)
            time.sleep(wait)

    async def connect_async(self) -> SerialConnection:
        """
        Similar to `connect_sync` but returns a coroutine instead of
        blocking the current thread while waiting to retry after failure.

        Note, actual connection attempts do run synchronously, blocking this
        loop thread. This is not typically unbounded but can take nontrivial
        time depending on the platform and its approach to USB and such.
        """

        while True:
            if conn := self.connect_sync(timeout=0):
                return conn
            with self._lock:
                wait = from_deadline(self._next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)
