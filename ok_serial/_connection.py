import asyncio
import contextlib
import dataclasses
import errno
import logging
import serial
import threading

from ok_serial import _exceptions
from ok_serial import _locking
from ok_serial import _scanning
from ok_serial import _timeout_math

log = logging.getLogger("ok_serial.connection")
data_log = logging.getLogger(log.name + ".data")


@dataclasses.dataclass(frozen=True)
class SerialOptions:
    baud: int = 115200
    sharing: _locking.SerialSharingType = "exclusive"


@dataclasses.dataclass(frozen=True)
class SerialSignals:
    dtr: bool
    dsr: bool
    cts: bool
    rts: bool
    ri: bool
    cd: bool
    sending_break: bool


class SerialConnection(contextlib.AbstractContextManager):
    def __init__(
        self,
        *,
        match: str | _scanning.SerialPortMatcher | None = None,
        port: str | _scanning.SerialPort | None = None,
        opts: SerialOptions = SerialOptions(),
        **kwargs,
    ):
        assert bool(match) + bool(port) == 1, "Need one of match or port"
        opts = dataclasses.replace(opts, **kwargs)

        if match:
            if isinstance(match, str):
                match = _scanning.SerialPortMatcher(match)
            found = _scanning.scan_serial_ports(match)
            if len(found) == 0:
                msg = f'No ports match "{match}"'
                raise _exceptions.SerialOpenException(msg)
            elif len(found) > 1:
                found_text = "".join(f"\n  {p}" for p in found)
                msg = f'Multiple ports match "{match}": {found_text}'
                raise _exceptions.SerialOpenException(msg)
            else:
                port = found[0].name
                log.debug("Scanned %r, found %s", match, port)

        assert port
        if isinstance(port, _scanning.SerialPort):
            port = port.name

        with contextlib.ExitStack() as cleanup:
            cleanup.enter_context(_locking.using_lock_file(port, opts.sharing))

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
                cleanup.enter_context(_locking.using_fd_lock(port, fd, share))

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
        self._cleanup.close()

    @property
    def port_name(self) -> str:
        return self._io.pyserial.port

    @property
    def pyserial(self) -> serial.Serial:
        return self._io.pyserial

    def fileno(self) -> int:
        try:
            return self._io.serial.fileno()
        except AttributeError:
            return -1

    def set_signals(
        self,
        dtr: bool | None = None,
        rts: bool | None = None,
        send_break: bool | None = None,
    ) -> None:
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

    def get_signals(self) -> SerialSignals:
        with self._io.monitor:
            if self._io.exception:
                raise self._io.exception
            try:
                return SerialSignals(
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

    def read_sync(
        self,
        *,
        min: int = 1,
        max: int = 65536,
        timeout: float | None = None,
    ) -> bytes:
        deadline = _timeout_math.to_deadline(timeout)
        while True:
            with self._io.monitor:
                if len(self._io.incoming) >= min:
                    incoming = self._io.incoming[:max]
                    del self._io.incoming[:max]
                    return incoming
                elif self._io.exception:
                    raise self._io.exception
                else:
                    wait = _timeout_math.from_deadline(deadline)
                    if wait <= 0:
                        return b""
                    self._io.monitor.wait(timeout=wait)

    async def read_async(self, *, min: int = 1, max: int = 65536) -> bytes:
        while True:
            future = self._io.create_future_in_loop()  # BEFORE read_sync
            out = self.read_sync(min=min, max=max, timeout=0)
            if out or min <= 0:
                return out
            await future

    def write(self, data: bytes) -> None:
        with self._io.monitor:
            if self._io.exception:
                raise self._io.exception
            elif data:
                self._io.outgoing.extend(data)
                self._io.monitor.notify_all()

    def drain_sync(self, *, max: int = 0, timeout: float | None = None) -> bool:
        deadline = _timeout_math.to_deadline(timeout)
        while True:
            with self._io.monitor:
                if self._io.exception:
                    raise self._io.exception
                elif len(self._io.outgoing) <= max:
                    return True
                else:
                    wait = _timeout_math.from_deadline(deadline)
                    if wait <= 0:
                        return False
                    self._io.monitor.wait(timeout=wait)

    async def drain_async(self, max: int = 0) -> bool:
        while True:
            future = self._io.create_future_in_loop()  # BEFORE drain_sync
            if self.drain_sync(max=max, timeout=0):
                return True
            await future

    def incoming_size(self) -> int:
        with self._io.monitor:
            return len(self._io.incoming)

    def outgoing_size(self) -> int:
        with self._io.monitor:
            return len(self._io.outgoing)


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
