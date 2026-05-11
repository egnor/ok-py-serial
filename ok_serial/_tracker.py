import asyncio
import contextlib
import dataclasses
import logging
import threading
import time

from ok_serial._connection import SerialConnection, SerialConnectionOptions
from ok_serial._exceptions import (
    SerialIoClosed,
    SerialIoException,
    SerialOpenException,
)
from ok_serial._matching import compile_match
from ok_serial._metadata import SerialPort, PortPredicate
from ok_serial._scanning import scan_serial_ports
from ok_serial._timeout_math import from_deadline, to_deadline

log = logging.getLogger("ok_serial.tracker")


@dataclasses.dataclass(frozen=True)
class TrackerOptions:
    """Optional parameters for `SerialPortTracker`."""

    scan_interval: float | int = 0.5
    """Seconds between port re-scans when waiting for a match."""


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
        topts: TrackerOptions = TrackerOptions(),
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

        Actual port scans and connections only happen after `find_*` or
        `connect_*` methods are called. Call `close` to end any open
        connection; use `SerialPortTracker` as the target of a
        [`with` statement](https://docs.python.org/3/reference/compound_stmts.html#with)
        to automatically close the port on exit from the `with` body.
        """

        if baud:
            copts = dataclasses.replace(copts, baud=baud)

        self.match = match
        self._match = compile_match(match)
        self._tracker_opts = topts
        self._conn_opts = copts

        self._lock = threading.Lock()
        self._scan_keys: set[str] = set()
        self._scan_matched: list[SerialPort] = []
        self._scan_deadline = 0.0
        self._next_scan = 0.0
        self._conn: SerialConnection | None = None

        log.debug("Tracking: %r", match or "(any port)")

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

        Returns `None` on timeout.

        Raises:
        - `SerialScanException` - System error scanning ports
        """

        deadline = to_deadline(timeout)
        while True:
            with self._lock:
                # Return an existing live connection if possible
                if self._conn:
                    try:
                        self._conn.write(b"")  # check for liveness
                        return self._conn
                    except SerialIoClosed:
                        log.debug("Conn to %s closed", self._conn.port_name)
                        self._conn.close()  # make sure _fully_ closed
                        self._conn = None
                    except SerialIoException as exc:
                        name = self._conn.port_name
                        log.warning("Conn to %s failed (%s)", name, exc)
                        self._conn.close()
                        self._conn = None

                # Re-scan for ports at the specified interval
                if (wait := from_deadline(self._next_scan)) <= 0:
                    self._scan_matched = scan_serial_ports(self._match)
                    self._scan_matched.sort(
                        key=lambda p: p.attr.get("time", ""), reverse=True
                    )

                    self._scan_keys, old_keys = set(), self._scan_keys
                    for p in self._scan_matched:
                        key = p.name + "@" + p.attr.get("time", "")
                        self._scan_keys.add(key)
                        if self._next_scan and key not in old_keys:
                            p.attr["tracking"] = "new"

                    wait = self._tracker_opts.scan_interval
                    self._next_scan = to_deadline(wait)

                for port in list(self._scan_matched):
                    try:
                        log.debug("Opening %s", port)
                        opts = self._conn_opts
                        self._conn = SerialConnection(port=port, opts=opts)
                        return self._conn
                    except SerialOpenException as exc:
                        log.warning("Can't open %s (%s)", port, exc)
                        self._scan_matched = []  # cool down until re-scan

            if from_deadline(deadline) < wait:
                return None

            log.debug("Next scan in %.2fs", wait)
            time.sleep(wait)

    async def connect_async(self) -> SerialConnection:
        """
        Similar to `connect_sync` but returns a
        [`Future`](https://docs.python.org/3/library/asyncio-future.html#asyncio.Future)
        instead of blocking the current thread.
        """

        while True:
            if conn := self.connect_sync(timeout=0):
                return conn
            with self._lock:
                wait = from_deadline(self._next_scan)
            log.debug("Next scan in %.2fs", wait)
            await asyncio.sleep(wait)
