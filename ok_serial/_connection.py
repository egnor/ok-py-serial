import asyncio
import contextlib
import dataclasses
import errno
import logging
import serial
import threading

from ok_serial import _exceptions
from ok_serial._locking import SerialSharingType, using_fd_lock, using_lock_file
from ok_serial._matcher import SerialPortMatcher
from ok_serial._scanning import SerialPort, scan_serial_ports
from ok_serial._timeout_math import from_deadline, to_deadline

log = logging.getLogger("ok_serial.connection")
data_log = logging.getLogger(log.name + ".data")


@dataclasses.dataclass(frozen=True)
class SerialConnectionOptions:
    """Optional parameters for `SerialConnection`."""

    baud: int = 115200
    """The [baud rate](https://en.wikipedia.org/wiki/Baud) to use."""
    sharing: SerialSharingType = "exclusive"
    """
    Port access negotiation strategy:
    - `"oblivious"`: Don't perform any locking.
    - `"polite"`: Defer to other users, don't lock the port.
    - `"exclusive":` Require exclusive access, lock the port or fail.
    - `"stomp"`: Try to kill other users, try to lock the port, open the
      port regardless. Use with care!
    """


@dataclasses.dataclass(frozen=True)
class SerialControlSignals:
    """
    [RS-232 modem control lines](https://en.wikipedia.org/wiki/RS-232#Data_and_control_signals),
    outgoing ("DTE to DCE") and incoming ("DCE to DTE").
    """

    dtr: bool
    dsr: bool
    cts: bool
    rts: bool
    ri: bool
    cd: bool
    sending_break: bool


class SerialConnection(contextlib.AbstractContextManager):
    """An open connection to a serial port."""

    def __init__(
        self,
        *,
        match: str | SerialPortMatcher | None = None,
        port: str | SerialPort | None = None,
        opts: SerialConnectionOptions = SerialConnectionOptions(),
        **kwargs,
    ):
        """
        Opens a serial port to make it available for use.
        - `match` can be a [port match expression](https://github.com/egnor/ok-py-serial#serial-port-match-expressions) matching exactly one port...
          - OR `port` must name a raw system serial device to open.
        - `opts` can define baud rate and other port parameters...
          - OR other keywords are forwarded to `SerialConnectionOptions`

        Call `close` to release the port; use
        `SerialConnection` as the target of a
        [`with` statement](https://docs.python.org/3/reference/compound_stmts.html#with)
        to automatically close the port on exit from the `with` body.

        Example:
        ```
        with SerialConnection(match="vid_pid=0403:6001", baud=115200, sharing="polite") as p:
            ... interact with `p` ...
            # automatically closed on exit from block
        ```

        Raises:
        - `SerialOpenException`: I/O error opening the specified port
        - `SerialOpenBusy`: The port is already in use
        - `SerialScanException`: System error scanning ports to find `match`
        - `SerialMatcherInvalid`: Bad format of `match` string
        """

        assert (match is not None) + (port is not None) == 1
        opts = dataclasses.replace(opts, **kwargs)

        if match is not None:
            if isinstance(match, str):
                match = SerialPortMatcher(match)
            if not (found := scan_serial_ports()):
                raise _exceptions.SerialOpenException("No ports found")
            if not (matched := match.filter(found)):
                msg = f"No ports match {match!r}"
                raise _exceptions.SerialOpenException(msg)
            if len(matched) > 1:
                matched_text = "".join(f"\n  {p}" for p in matched)
                msg = f'Multiple ports match "{match}": {matched_text}'
                raise _exceptions.SerialOpenException(msg)
            port = matched[0].name
            log.debug("Scanned %r, found %s", match, port)

        assert port is not None
        if isinstance(port, SerialPort):
            port = port.name

        with contextlib.ExitStack() as cleanup:
            cleanup.enter_context(using_lock_file(port, opts.sharing))

            try:
                pyserial = cleanup.enter_context(
                    serial.Serial(
                        port=port,
                        baudrate=opts.baud,
                        write_timeout=0.1,
                    )
                )
                log.debug("Opened %s %s", port, opts)
            except OSError as ex:
                if ex.errno == errno.EBUSY:
                    msg = "Serial port busy (EBUSY)"
                    raise _exceptions.SerialOpenBusy(msg, port) from ex
                else:
                    msg = "Serial port open error"
                    raise _exceptions.SerialOpenException(msg, port) from ex

            if hasattr(pyserial, "fileno"):
                fd, share = pyserial.fileno(), opts.sharing
                cleanup.enter_context(using_fd_lock(port, fd, share))

            self._io = cleanup.enter_context(_IoThreads(pyserial))
            self._io.start()
            self._cleanup = cleanup.pop_all()

    def __del__(self) -> None:
        if hasattr(self, "_cleanup"):
            self._cleanup.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._cleanup.__exit__(exc_type, exc_value, traceback)

    def __repr__(self) -> str:
        return f"SerialConnection({self._io.pyserial.port!r})"

    def close(self) -> None:
        """
        Releases the serial port connection and any associated locks.

        Any I/O operations in progress or attempted after closure will
        raise an immediate `SerialIoClosed` exception.
        """

        self._cleanup.close()

    def read_sync(
        self,
        *,
        timeout: float | int | None = None,
    ) -> bytes:
        """
        Waits up to `timeout` seconds (forever for `None`) for data,
        then returns all of it (b"" on timeout).

        Raises:
        - `SerialIoException`: port I/O failed and there is no matching data
        - `SerialIoClosed`: the port was closed and there is no matching data
        """

        deadline = to_deadline(timeout)
        while True:
            with self._io.monitor:
                if self._io.incoming:
                    output = bytes(self._io.incoming)
                    self._io.incoming.clear()
                    return output
                elif self._io.exception:
                    raise self._io.exception
                elif (wait := from_deadline(deadline)) <= 0:
                    return b""
                else:
                    self._io.monitor.wait(timeout=wait)

    async def read_async(self) -> bytes:
        """
        Similar to `read_sync` but returns a
        [`Future`](https://docs.python.org/3/library/asyncio-future.html#asyncio.Future)
        instead of blocking the current thread.
        """

        while True:
            future = self._io.create_future_in_loop()  # BEFORE read_sync
            if out := self.read_sync(timeout=0):
                return out
            await future

    def write(self, data: bytes | bytearray) -> None:
        """
        Adds data to the outgoing buffer to be sent immediately.
        Never blocks; the buffer can grow indefinitely.
        (Use `outgoing_size` and `drain_sync`/`drain_async` to manage
        buffer size.)

        Raises:
        - `SerialIoException`: port I/O failed
        - `SerialIoClosed`: the port was closed
        """

        with self._io.monitor:
            if self._io.exception:
                raise self._io.exception
            elif data:
                self._io.outgoing.extend(data)
                self._io.monitor.notify_all()

    def drain_sync(self, *, timeout: float | int | None = None) -> bool:
        """
        Waits up to `timeout` seconds (forever for `None`) until
        all buffered data is transmitted.

        Returns `True` if the drain completed, `False` on timeout.

        Raises:
        - `SerialIoException`: port I/O failed
        - `SerialIoClosed`: the port was closed
        """

        deadline = to_deadline(timeout)
        while True:
            with self._io.monitor:
                if self._io.exception:
                    raise self._io.exception
                elif not self._io.outgoing:
                    return True
                elif (wait := from_deadline(deadline)) <= 0:
                    return False
                else:
                    self._io.monitor.wait(timeout=wait)

    async def drain_async(self) -> bool:
        """
        Similar to `drain_sync` but returns a
        [`Future`](https://docs.python.org/3/library/asyncio-future.html#asyncio.Future)
        instead of blocking the current thread.
        """

        while True:
            future = self._io.create_future_in_loop()  # BEFORE drain_sync
            if self.drain_sync(timeout=0):
                return True
            await future

    def incoming_size(self) -> int:
        """
        Returns the number of bytes waiting to be read.
        """
        with self._io.monitor:
            return len(self._io.incoming)

    def outgoing_size(self) -> int:
        """
        Returns the number of bytes waiting to be sent.
        """
        with self._io.monitor:
            return len(self._io.outgoing)

    def set_signals(
        self,
        dtr: bool | None = None,
        rts: bool | None = None,
        send_break: bool | None = None,
    ) -> None:
        """
        Sets outgoing
        [RS-232 modem control line](https://en.wikipedia.org/wiki/RS-232#Data_and_control_signals)
        state (use `None` for no change):
        - `dtr`: assert Data Terminal Ready
        - `rts`: assert Ready To Send
        - `send_break`: send a continuous BREAK condition

        Raises:
        - `SerialIoException`: port I/O failed
        - `SerialIoClosed`: the port was closed
        """

        with self._io.monitor:
            if self._io.exception:
                raise self._io.exception
            try:
                if dtr is not None:
                    self._io.pyserial.dtr = dtr
                if rts is not None:
                    self._io.pyserial.rts = rts
                if send_break is not None:
                    self._io.pyserial.break_condition = send_break
            except OSError as ex:
                msg, dev = "Can't set control signals", self._io.pyserial.port
                self._io.exception = _exceptions.SerialIoException(msg, dev)
                self._io.exception.__cause__ = ex
                raise self._io.exception

    def get_signals(self) -> SerialControlSignals:
        """
        Returns the current
        [RS-232 modem control line](https://en.wikipedia.org/wiki/RS-232#Data_and_control_signals) state.

        Raises:
        - `SerialIoException`: port I/O failed
        - `SerialIoClosed`: the port was closed
        """

        with self._io.monitor:
            if self._io.exception:
                raise self._io.exception
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
                msg, dev = "Can't get control signals", self._io.pyserial.port
                self._io.exception = _exceptions.SerialIoException(msg, dev)
                self._io.exception.__cause__ = ex
                raise self._io.exception

    @property
    def port_name(self) -> str:
        """
        The port's device name, eg. `/dev/ttyACM0` or `COM3`.
        """
        return self._io.pyserial.port

    @property
    def pyserial(self) -> serial.Serial:
        """
        The underlying
        [`pyserial.Serial`](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial)
        object (API escape hatch).
        """
        return self._io.pyserial

    def fileno(self) -> int:
        """
        The [Unix FD](https://en.wikipedia.org/wiki/File_descriptor)
        for the serial connection, -1 if not available.
        """
        try:
            return self._io.serial.fileno()
        except AttributeError:
            return -1


class _IoThreads(contextlib.AbstractContextManager):
    def __init__(self, pyserial: serial.Serial) -> None:
        self.threads: list[threading.Thread] = []
        self.pyserial = pyserial
        self.monitor = threading.Condition()
        self.incoming = bytearray()
        self.outgoing = bytearray()
        self.exception: None | _exceptions.SerialIoException = None
        self.async_futures: list[asyncio.Future[None]] = []
        self.async_loop: asyncio.AbstractEventLoop | None
        try:
            self.async_loop = asyncio.get_running_loop()
        except RuntimeError:
            self.async_loop = None

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()

    def start(self):
        for t, n in ((self._readloop, "reader"), (self._writeloop, "writer")):
            dev = self.pyserial.port
            thread = threading.Thread(target=t, name=f"{dev} {n}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        with self.monitor:
            if not isinstance(self.exception, _exceptions.SerialIoClosed):
                msg, dev = "Serial port closed", self.pyserial.port
                exc = _exceptions.SerialIoClosed(msg, dev)
                exc.__context__, self.exception = self.exception, exc
                self._notify_all_locked()

        try:
            self.pyserial.cancel_read()
            self.pyserial.cancel_write()
            log.debug("Cancelled %s I/O", self.pyserial.port)
        except OSError as ex:
            log.warning("Can't cancel %s I/O (%s)", self.pyserial.port, ex)

        log.debug("Joining %s I/O threads", self.pyserial.port)
        for thr in self.threads:
            thr.join()

    def _readloop(self) -> None:
        log.debug("Starting thread")
        while not self.exception:
            incoming, error = b"", None
            try:
                # Block for at least one byte, then grab all available
                incoming = self.pyserial.read(size=1)
                if incoming:
                    waiting = self.pyserial.in_waiting
                    if waiting > 0:
                        incoming += self.pyserial.read(size=waiting)
            except OSError as ex:
                msg, dev = "Serial read error", self.pyserial.port
                error = _exceptions.SerialIoException(msg, dev)
                error.__cause__ = ex
                data_log.warning("%s (%s)", msg, ex)

            with self.monitor:
                if incoming:
                    data_log.debug(
                        "Read %db buf=%db", len(incoming), len(self.incoming)
                    )
                if incoming or error:
                    self.incoming.extend(incoming)
                    self.exception = self.exception or error
                    self._notify_all_locked()

    def _writeloop(self) -> None:
        log.debug("Starting thread")

        # Avoid blocking on writes to avoid pyserial bugs:
        # https://github.com/pyserial/pyserial/issues/280
        # https://github.com/pyserial/pyserial/issues/281
        chunk, error = b"", None
        while not self.exception:
            if chunk:
                try:
                    self.pyserial.write(chunk)
                    self.pyserial.flush()
                except OSError as ex:
                    chunk = b""
                    msg, dev = "Serial write error", self.pyserial.port
                    error = _exceptions.SerialIoException(msg, dev)
                    error.__cause__ = ex
                    data_log.warning("%s (%s)", msg, ex)

            with self.monitor:
                if chunk:
                    assert self.outgoing.startswith(chunk)
                    chunk_len, outgoing_len = len(chunk), len(self.outgoing)
                    data_log.debug("Wrote %d/%db", chunk_len, outgoing_len)
                    del self.outgoing[:chunk_len]
                if chunk or error:
                    self.exception = self.exception or error
                    self._notify_all_locked()
                while not self.exception and not self.outgoing:
                    self.monitor.wait()
                chunk = self.outgoing[:256]

    def _notify_all_locked(self) -> None:
        """Must be run with self.monitor lock held."""

        self.monitor.notify_all()
        if self.async_futures:
            assert self.async_loop
            self.async_loop.call_soon_threadsafe(self._resolve_futures_in_loop)

    def create_future_in_loop(self) -> asyncio.Future[None]:
        """Must be run from an asyncio event loop."""

        assert self.async_loop
        with self.monitor:
            future = self.async_loop.create_future()
            self.async_futures.append(future)
            dev, nf = self.pyserial.port, len(self.async_futures)
            data_log.debug("%s: Adding async future -> %d total", dev, nf)
            return future

    def _resolve_futures_in_loop(self) -> None:
        """Must be run from an asyncio event loop."""

        # Exceptions will be handled by the event loop exception handler
        assert self.async_loop
        with self.monitor:
            to_resolve, self.async_futures = self.async_futures, []

        dev = self.pyserial.port
        data_log.debug("%s: Waking %d async futures", dev, len(to_resolve))
        for future in to_resolve:
            if not future.done():
                future.set_result(None)
