import asyncio
import contextlib
import dataclasses
import errno
import logging
import serial
import threading
import time

from ok_serial import _exceptions
from ok_serial._lock import PortLock, SerialSharingType
from ok_serial._port import SerialPort, PortPredicate
from ok_serial._scan import scan_serial_ports
from ok_serial._timeout_math import from_deadline, to_deadline

log = logging.getLogger("ok_serial.connection")
data_log = logging.getLogger(log.name + ".data")


@dataclasses.dataclass(frozen=True)
class SerialConnectionOptions:
    """Optional parameters for `SerialConnection`."""

    baud: int = 115200
    """The [baud rate](https://en.wikipedia.org/wiki/Baud) to use."""

    sharing: SerialSharingType = "exclusive"
    """Port access negotiation strategy.

    - `"oblivious"` - Don't perform any locking.
    - `"polite"` - Defer to any other use of the port; don't lock the port.
    - `"exclusive"` - Require exclusive access; lock the port or fail.
    - `"stomp"` - Try to kill other processes using the port, try to lock the
      port, open the port regardless. Use with care!
    """


@dataclasses.dataclass(frozen=True)
class SerialControlSignals:
    """[RS-232 modem control lines](https://en.wikipedia.org/wiki/RS-232#Data_and_control_signals).

    Includes outgoing ("DTE to DCE") and incoming ("DCE to DTE") signals.
    """

    dtr: bool
    dsr: bool
    cts: bool
    rts: bool
    ri: bool
    cd: bool
    sending_break: bool


class TimestampBytes(bytes):
    """Bytes received from a serial port, plus a monotonic-time stamp."""

    monotonic_time: float

    def __new__(cls, data: bytes | bytearray, time: float) -> "TimestampBytes":
        self = super().__new__(cls, data)
        self.monotonic_time = time
        return self

    def __repr__(self) -> str:
        return f"{bytes(self)!r}@t={self.monotonic_time:.3f}"


class SerialConnection(contextlib.AbstractContextManager):
    """An open connection to a serial port.

    Thread-safe: any method may be called from any thread any time, and any
    `*_async` method may be awaited from any event loop on any thread any time.
    """

    def __init__(
        self,
        *,
        match: str | PortPredicate | None = None,
        port: str | SerialPort | None = None,
        opts: SerialConnectionOptions = SerialConnectionOptions(),
        **kwargs,
    ):
        """Opens a serial port to make it available for use.

        - `match` is a
          [match string](https://github.com/egnor/ok-py-serial#port-matching)
          or `SerialPort -> bool` callable matching exactly one port...
          - OR `port` must name a raw system serial device to open.
        - `opts` can define baud rate and other port parameters...
          - OR other keywords are forwarded to `SerialConnectionOptions`

        Call `close` to release the port, or use `SerialConnection` as the
        target of a `with` statement.

        Example:
        ```
        with SerialConnection(match="xyz", baud=115200, sharing="polite") as p:
            ... interact with `p` ...
            # automatically closed on exit from block
        ```

        Raises:
        - `SerialOpenException` - I/O error opening the specified port
        - `SerialOpenBusy` - The port is already in use
        - `SerialScanException` - System error scanning ports to find `match`
        """

        assert (match is not None) + (port is not None) == 1
        self._opts = dataclasses.replace(opts, **kwargs)

        if match is not None:
            if not (found := scan_serial_ports(match)):
                msg = f"No ports match {match!r}"
                raise _exceptions.SerialOpenException(msg)
            if len(found) > 1:
                detail = "".join(f"\n  {p}" for p in found)
                msg = f"Multiple ports match {match!r}: {detail}"
                raise _exceptions.SerialOpenException(msg)
            port = found[0].name
            log.debug("Scanned %r, found %s", match, port)

        assert port is not None
        if isinstance(port, SerialPort):
            port = port.name

        with contextlib.ExitStack() as cleanup:
            port_lock = cleanup.enter_context(
                PortLock(port, self._opts.sharing)
            )

            try:
                # (If "polite", wake the readloop periodically for checks.)
                timeout = 0.5 if self._opts.sharing == "polite" else None
                pyserial = cleanup.enter_context(
                    serial.Serial(
                        port=port,
                        baudrate=self._opts.baud,
                        write_timeout=0.1,
                        timeout=timeout,
                    )
                )
                log.debug("Opened %s %s", port, self._opts)
            except OSError as ex:
                if ex.errno == errno.EBUSY:
                    msg = "Port busy (EBUSY)"
                    raise _exceptions.SerialOpenBusy(msg, port) from ex
                else:
                    msg = "Port open error"
                    raise _exceptions.SerialOpenException(msg, port) from ex

            if hasattr(pyserial, "fileno"):
                # unlock fd before closing port (see note on release_fd)
                cleanup.callback(port_lock.release_fd)
                port_lock.attach_fd(pyserial.fileno())

            # (annotated because AbstractContextManager.__enter__ gives Any,
            # which would silently disable checking of every self._io use)
            self._io: _IoThreads = cleanup.enter_context(
                _IoThreads(pyserial, port_lock)
            )
            self._io.start()
            self._cleanup_lock = threading.Lock()  # assigned before _cleanup
            self._cleanup = cleanup.pop_all()

    def __del__(self) -> None:
        if cleanup := getattr(self, "_cleanup", None):
            with self._cleanup_lock:
                cleanup.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        with self._cleanup_lock:
            self._cleanup.__exit__(exc_type, exc_value, traceback)

    def __repr__(self) -> str:
        return f"SerialConnection({self._io.device!r})"

    def close(self) -> None:
        """Releases the serial port connection and any associated locks.

        Blocks until connection I/O threads have finished.
        Thread-safe, OK to call repeatedly, and OK to call with I/O in flight.
        Any I/O operations in progress or attempted afterwards raise an
        immediate `SerialIoClosed` exception.
        """

        with self._cleanup_lock:
            self._cleanup.close()

    def read_sync(
        self,
        *,
        timeout: float | int | None = None,
    ) -> TimestampBytes:
        """Waits up to `timeout` seconds (forever for `None`) for any data,
        then returns all of it (b"" on timeout).

        Thread-safe, but each call takes all the currently buffered data.

        Raises:
        - `SerialIoException` - port I/O failed and there is no matching data
        - `SerialIoClosed` - the port was closed and there is no matching data
        """

        deadline = to_deadline(timeout)
        while True:
            with self._io.monitor:
                if self._io.incoming:
                    monotime = self._io.incoming_monotime
                    out = TimestampBytes(self._io.incoming, monotime)
                    self._io.incoming.clear()
                    self._io.incoming_monotime = 0.0
                    return out

                self._io.check_poison_locked()
                if (wait := from_deadline(deadline)) <= 0:
                    return TimestampBytes(b"", 0.0)
                self._io.monitor.wait(timeout=wait)

    async def read_async(self) -> TimestampBytes:
        """Similar to `read_sync` but returns a coroutine instead of blocking.

        OK to call from any event loop on any thread, but as with `read_sync`
        each call takes all the currently buffered data.

        Raises `RuntimeError` if there is no running event loop.
        """

        while True:
            future = self._io.create_future_in_loop()  # BEFORE read_sync
            if out := self.read_sync(timeout=0):
                return out
            await future

    def write(self, data: bytes | bytearray) -> None:
        """Adds data to the outgoing buffer to be sent immediately.

        Never blocks; the buffer can grow indefinitely. (Use `outgoing_size`
        and `drain_sync`/`drain_async` to manage buffer size.)

        Raises:
        - `SerialIoException` - port I/O failed
        - `SerialIoClosed` - the port was closed
        """

        with self._io.monitor:
            self._io.check_poison_locked()
            if data:
                self._io.outgoing.extend(data)
                self._io.monitor.notify_all()

    def drain_sync(self, *, timeout: float | int | None = None) -> bool:
        """Waits up to `timeout` seconds (forever for `None`) until
        all buffered data is transmitted.

        Returns `True` if the drain completed, `False` on timeout.

        Raises:
        - `SerialIoException` - port I/O failed
        - `SerialIoClosed` - the port was closed
        """

        deadline = to_deadline(timeout)
        while True:
            with self._io.monitor:
                self._io.check_poison_locked()
                if not self._io.outgoing:
                    return True
                if (wait := from_deadline(deadline)) <= 0:
                    return False
                self._io.monitor.wait(timeout=wait)

    async def drain_async(self) -> bool:
        """Similar to `drain_sync` but returns a coroutine instead of blocking.

        Raises `RuntimeError` if there is no running event loop.
        """

        while True:
            future = self._io.create_future_in_loop()  # BEFORE drain_sync
            if self.drain_sync(timeout=0):
                return True
            await future

    def incoming_size(self) -> int:
        """Returns the number of bytes waiting to be read."""
        with self._io.monitor:
            return len(self._io.incoming)

    def outgoing_size(self) -> int:
        """Returns the number of bytes waiting to be sent."""
        with self._io.monitor:
            return len(self._io.outgoing)

    def set_signals(
        self,
        dtr: bool | None = None,
        rts: bool | None = None,
        send_break: bool | None = None,
    ) -> None:
        """Sets outgoing
        [RS-232 modem control line](https://en.wikipedia.org/wiki/RS-232#Data_and_control_signals)
        state (use `None` for no change).

        - `dtr` - assert Data Terminal Ready
        - `rts` - assert Ready To Send
        - `send_break` - send a continuous BREAK condition

        Raises:
        - `SerialIoException` - port I/O failed
        - `SerialIoClosed` - the port was closed
        """

        with self._io.monitor:
            self._io.check_poison_locked()
            try:
                if dtr is not None:
                    self._io.pyserial.dtr = dtr
                if rts is not None:
                    self._io.pyserial.rts = rts
                if send_break is not None:
                    self._io.pyserial.break_condition = send_break
            except OSError as ex:
                msg, dev = "Can't set control signals", self._io.device
                if ex.errno == errno.ENOTTY:  # could be pty; don't poison
                    raise _exceptions.SerialIoUnsupported(msg, dev) from ex
                self._io.poison_locked(_exceptions.SerialIoException, msg, ex)
                self._io.check_poison_locked()  # report to the caller

    def get_signals(self) -> SerialControlSignals:
        """Returns the current
        [RS-232 modem control line](https://en.wikipedia.org/wiki/RS-232#Data_and_control_signals) state.

        Raises:
        - `SerialIoException` - port I/O failed
        - `SerialIoClosed` - the port was closed
        """

        with self._io.monitor:
            self._io.check_poison_locked()
            try:
                return SerialControlSignals(
                    dtr=self._io.pyserial.dtr,
                    dsr=self._io.pyserial.dsr,
                    cts=self._io.pyserial.cts,
                    rts=self._io.pyserial.rts,
                    ri=self._io.pyserial.ri,
                    cd=self._io.pyserial.cd,
                    sending_break=self._io.pyserial.break_condition,
                )
            except OSError as ex:
                msg, dev = ("Can't get control signals", self._io.device)
                if ex.errno == errno.ENOTTY:  # could be pty; don't poison
                    raise _exceptions.SerialIoUnsupported(msg, dev) from ex
                self._io.poison_locked(_exceptions.SerialIoException, msg, ex)
                self._io.check_poison_locked()  # report to the caller
                assert False, "check_poison_locked() should have raised"

    @property
    def port_name(self) -> str:
        """The port's device name, eg. `/dev/ttyACM0` or `COM3`."""
        return self._io.device

    @property
    def pyserial(self) -> serial.Serial:
        """The underlying
        [`pyserial.Serial`](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial)
        object (API escape hatch).

        NOT SYNCHRONIZED. Use at your own risk.
        """
        return self._io.pyserial

    def fileno(self) -> int:
        """The [Unix FD](https://en.wikipedia.org/wiki/File_descriptor)
        for the serial connection, -1 if not available.
        """
        pyserial = self._io.pyserial
        try:
            return pyserial.fileno()
        except AttributeError:  # no fileno() at all (eg. on Windows)
            return -1
        except OSError:  # port closed, or otherwise has no descriptor
            return -1


class _IoThreads(contextlib.AbstractContextManager):
    def __init__(
        self,
        pyserial: serial.Serial,
        port_lock: PortLock | None = None,
    ) -> None:
        assert pyserial.port is not None
        self.device: str = pyserial.port  # pyserial's is typed `str | None`
        self.pyserial = pyserial
        self.monitor = threading.Condition()
        self.outgoing = bytearray()
        self.incoming = bytearray()
        self.incoming_monotime = 0.0
        self._poison_type: type[_exceptions.SerialIoException] | None = None
        self._poison_message: str | None = None
        self._poison_cause: BaseException | None = None
        self._port_lock = port_lock
        self._threads: list[threading.Thread] = []

        # Async futures grouped by the event loop to dispatch to.
        NotifyType = dict[asyncio.AbstractEventLoop, list[asyncio.Future[None]]]
        self.async_notify: NotifyType = {}

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()

    def start(self):
        for t, n in ((self._readloop, "reader"), (self._writeloop, "writer")):
            dev = self.device
            thread = threading.Thread(target=t, name=f"{dev} {n}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self):
        with self.monitor:
            self.poison_locked(_exceptions.SerialIoClosed, "Serial port closed")

        try:
            self.pyserial.cancel_read()
            self.pyserial.cancel_write()
            log.debug("Cancelled %s I/O", self.device)
        except OSError as ex:
            log.warning("Can't cancel %s I/O (%s)", self.device, ex)

        log.debug("Joining %s I/O threads", self.device)
        for thr in self._threads:
            thr.join()

    def create_future_in_loop(self) -> asyncio.Future[None]:
        """Returns a future (on the running loop) resolved on state change."""
        loop = asyncio.get_running_loop()
        with self.monitor:
            future = loop.create_future()
            self.async_notify.setdefault(loop, []).append(future)
            return future

    def poison_locked(
        self,
        exc_type: type[_exceptions.SerialIoException],
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        """Marks the connection dead. (Must call with self.monitor.)"""
        if (
            issubclass(exc_type, _exceptions.SerialIoClosed)
            and self._poison_type is not None
            and not issubclass(self._poison_type, _exceptions.SerialIoClosed)
        ):  # override non-SerialIoClosed with SerialIoClosed
            message += f" (after {self._poison_message})"
            cause = cause or self._poison_cause
        elif self._poison_type:
            return  # keep the first error; no change -> no notify needed

        self._poison_type = exc_type
        self._poison_message = message
        self._poison_cause = cause
        self._notify_all_locked()

    def check_poison_locked(self) -> None:
        """Raises if the connection is dead. (Must call with self.monitor.)"""
        if self._poison_type:
            msg = self._poison_message or "Serial error"
            if cause := self._poison_cause:
                raise self._poison_type(msg, self.device) from cause
            raise self._poison_type(msg, self.device)

    def _readloop(self) -> None:
        log.debug("Starting thread")
        while not self._poison_type:
            incoming, exc, monotonic_time = b"", None, 0.0
            try:
                # Block for at least one byte, then grab all available
                incoming = self.pyserial.read(size=1)
                monotonic_time = time.monotonic()
                if self._port_lock:
                    self._port_lock.check()
                if incoming:
                    waiting = self.pyserial.in_waiting
                    if waiting > 0:
                        incoming += self.pyserial.read(size=waiting)
            except OSError as ex:  # includes SerialIoException
                exc = ex
                data_log.debug("Read: %s", ex)

            with self.monitor:
                if incoming:
                    if not self.incoming:
                        self.incoming_monotime = monotonic_time
                    self.incoming.extend(incoming)
                    in_len, buf_len = len(incoming), len(self.incoming)
                    data_log.debug("Read %db -> buf=%db", in_len, buf_len)
                if isinstance(exc, _exceptions.SerialIoException):
                    self.poison_locked(type(exc), exc.message, exc.__cause__)
                elif exc:
                    msg = "Serial read error"
                    self.poison_locked(_exceptions.SerialIoException, msg, exc)
                else:
                    self._notify_all_locked()

    def _writeloop(self) -> None:
        log.debug("Starting thread")
        chunk = b""

        # Avoid blocking on writes to avoid pyserial bugs:
        # https://github.com/pyserial/pyserial/issues/280
        # https://github.com/pyserial/pyserial/issues/281
        while not self._poison_type:
            exc = None
            if chunk:
                try:
                    self.pyserial.write(chunk)
                    self.pyserial.flush()
                except OSError as ex:
                    exc = ex
                    chunk = b""
                    data_log.debug("Write: %s", ex)

            with self.monitor:
                if chunk:
                    assert self.outgoing.startswith(chunk)
                    chunk_len, outgoing_len = len(chunk), len(self.outgoing)
                    data_log.debug("Wrote %d/%db", chunk_len, outgoing_len)
                    del self.outgoing[:chunk_len]
                if exc:
                    msg = "Serial write error"
                    self.poison_locked(_exceptions.SerialIoException, msg, exc)
                elif chunk:
                    self._notify_all_locked()
                while not self._poison_type and not self.outgoing:
                    self.monitor.wait()
                chunk = bytes(self.outgoing[:256])

    def _notify_all_locked(self) -> None:
        """Ping sync and async state watchers. (Must call with self.monitor.)"""
        self.monitor.notify_all()
        for loop, futures in self.async_notify.items():
            try:
                loop.call_soon_threadsafe(_resolve_futures_in_loop, futures)
            except RuntimeError:  # loop closed with waiters still registered
                log.debug("Dropped waiters for closed event loop")
        self.async_notify.clear()


def _resolve_futures_in_loop(futures: list[asyncio.Future[None]]) -> None:
    """Resolves all of `futures`. (Must run in their event loop.)"""

    for future in futures:
        if not future.done():
            future.set_result(None)
