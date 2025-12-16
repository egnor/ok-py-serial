import asyncio
import contextlib
import errno
import logging
import serial
import threading

import pydantic

from ok_serial import _exceptions
from ok_serial import _locking
from ok_serial import _timeout_math

log = logging.getLogger("ok_serial.connection")
data_log = logging.getLogger(log.name + ".data")


class SerialOptions(pydantic.BaseModel):
    baud: int = 115200
    sharing: _locking.SerialSharingType = "exclusive"


class SerialConnection(contextlib.AbstractContextManager):
    @pydantic.validate_call
    def __init__(self, port: str, opts: SerialOptions | int = SerialOptions()):
        if isinstance(opts, int):
            opts = SerialOptions(baud=opts)

        with contextlib.ExitStack() as cleanup:
            cleanup.enter_context(_locking.using_lock_file(port, opts.sharing))

            log.debug("Opening %s (%s)", port, opts)
            try:
                pyserial = cleanup.enter_context(
                    serial.Serial(
                        port=port,
                        baudrate=opts.baud,
                        write_timeout=0.1,
                    )
                )
            except OSError as ex:
                if ex.errno == errno.EBUSY:
                    message = "Serial port busy (EBUSY)"
                    raise _exceptions.SerialOpenBusy(message, port) from ex
                else:
                    message = "Serial port open error"
                    raise _exceptions.SerialOpenException(message, port) from ex

            if hasattr(pyserial, "fileno"):
                fd, sharing = pyserial.fileno(), opts.sharing
                cleanup.enter_context(_locking.using_fd_lock(port, fd, sharing))

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

    @pydantic.validate_call
    def close(self) -> None:
        self._cleanup.close()

    @pydantic.validate_call
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

    @pydantic.validate_call
    async def read_async(self, *, min: int = 1, max: int = 65536) -> bytes:
        while True:
            future = self._io.create_future_in_loop()  # BEFORE read_sync
            out = self.read_sync(min=min, max=max, timeout=0)
            if out or min <= 0:
                return out
            await future

    @pydantic.validate_call
    def write(self, data: bytes) -> None:
        with self._io.monitor:
            if self._io.exception:
                raise self._io.exception
            elif data:
                self._io.outgoing.extend(data)
                self._io.monitor.notify_all()

    @pydantic.validate_call
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

    @pydantic.validate_call
    async def drain_async(self, max: int = 0) -> bool:
        while True:
            future = self._io.create_future_in_loop()  # BEFORE drain_sync
            if self.drain_sync(max=max, timeout=0):
                return True
            await future

    @pydantic.validate_call
    def incoming_size(self) -> int:
        with self._io.monitor:
            return len(self._io.incoming)

    @pydantic.validate_call
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
            port = self.pyserial.port
            thread = threading.Thread(target=t, name=f"{port} {n}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        with self.monitor:
            if not self.exception:
                message, port = "Serial port was closed", self.pyserial.port
                self.exception = _exceptions.SerialIoClosed(message, port)
            self.monitor.notify_all()

        try:
            self.pyserial.cancel_read()
            self.pyserial.cancel_write()
            log.debug("Cancelled %s I/O", self.pyserial.port)
        except OSError:
            log.warn("Can't cancel %s I/O", self.pyserial.port, exc_info=True)

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
                message, port = "Serial read error", self.pyserial.port
                error = _exceptions.SerialIoException(message, port)
                error.__cause__ = ex
                data_log.warn("%s", message, exc_info=True)

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
                    message, port = "Serial write error", self.pyserial.port
                    error = _exceptions.SerialIoException(message, port)
                    error.__cause__ = ex
                    data_log.warn("%s", message, exc_info=True)

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
        """Must be run from asyncio event loop."""

        assert self.async_loop
        with self.monitor:
            future = self.async_loop.create_future()
            self.async_futures.append(future)
            data_log.debug(
                "%s: Adding async future -> %d total",
                self.pyserial.port,
                len(self.async_futures),
            )
            return future

    def _resolve_futures_in_loop(self) -> None:
        """Must be run from asyncio event loop."""

        assert self.async_loop
        with self.monitor:
            data_log.debug(
                "%s: Waking %d async futures",
                self.pyserial.port,
                len(self.async_futures),
            )
            while f := self.async_futures.pop():
                if not f.done():
                    f.set_result(None)
