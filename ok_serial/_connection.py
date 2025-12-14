import asyncio
import contextlib
import errno
import logging
import serial
import threading
import time

from ok_serial import _exceptions
from ok_serial import _locking

log = logging.getLogger(__name__)


class ConnectionClosedException(OSError):
    def __init__(self, message: str):
        super().__init__(message)


class SerialConnection(contextlib.AbstractContextManager):
    def __init__(
        self,
        port: str,
        *,
        baud: int = 115200,
        sharing: _locking.SharingType = "exclusive",
    ):
        with contextlib.ExitStack() as cleanup:
            self._port = port
            self._sharing = sharing

            cleanup.enter_context(_locking.using_lock_file(port, sharing))

            log.debug("Opening %s (%dbps, %s)", port, baud, sharing)
            try:
                pyserial = cleanup.enter_context(
                    serial.Serial(
                        port=port,
                        baudrate=baud,
                        write_timeout=0.1,
                    )
                )
            except OSError as exc:
                if exc.errno == errno.EBUSY:
                    message = "Serial port busy (EBUSY)"
                    raise _exceptions.SerialPortBusy(message, port) from exc
                else:
                    message = "Serial port open error"
                    raise _exceptions.SerialOpenFailed(message, port) from exc

            if hasattr(pyserial, "fileno"):
                fd = pyserial.fileno()
                cleanup.enter_context(_locking.using_fd_lock(port, fd, sharing))

            self._io = cleanup.enter_context(_IoThreads(pyserial))
            self._io.start()
            self._cleanup = cleanup.pop_all()

    def __del__(self) -> None:
        self._cleanup.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._cleanup.__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        self._cleanup.close()

    def read_sync(
        self,
        *,
        min: int = 1,
        max: int = 65536,
        timeout: float | None = None,
    ) -> bytes:
        deadline = _deadline_from_timeout(timeout)
        while True:
            with self._io.monitor:
                if len(self._io.incoming) >= min:
                    incoming = self._io.incoming[:max]
                    del self._io.incoming[:max]
                    return incoming
                elif self._io.exception:
                    raise self._io.exception
                else:
                    wait_timeout = _timeout_from_deadline(deadline)
                    if wait_timeout <= 0:
                        return b""
                    self._io.monitor.wait(timeout=wait_timeout)

    async def read_async(self, *, min: int = 1, max: int = 65536) -> bytes:
        while True:
            future = self._io.create_future()  # create BEFORE read_sync
            out = self.read_sync(min=min, max=max, timeout=0)
            if out or min <= 0:
                return out
            await future

    def write(self, data: bytes) -> None:
        with self._io.monitor:
            if self._io.exception:
                raise self._io.exception
            else:
                self._io.outgoing.extend(data)
                self._io.monitor.notify_all()

    def drain_sync(self, *, max: int = 0, timeout: float | None = None) -> bool:
        deadline = _deadline_from_timeout(timeout)
        while True:
            with self._io.monitor:
                if self._io.exception:
                    raise self._io.exception
                elif len(self._io.outgoing) <= max:
                    return True
                else:
                    wait_timeout = _timeout_from_deadline(deadline)
                    if wait_timeout <= 0:
                        return False
                    self._io.monitor.wait(timeout=wait_timeout)

    async def drain_async(self, max: int = 0) -> bool:
        while True:
            future = self._io.create_future()  # create BEFORE drain_sync
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
        self.exception: None | _exceptions.SerialIoFailed = None

        self.async_futures: list[asyncio.Future[None]] = []
        self.async_loop: asyncio.AbstractEventLoop | None
        try:
            self.async_loop = asyncio.get_running_loop()
        except RuntimeError:
            self.async_loop = None

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()

    def start(self):
        for t, n in ((self.read_loop, "reader"), (self.write_loop, "writer")):
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

    def read_loop(self) -> None:
        log.debug("Starting thread")
        while True:
            incoming, error = b"", None
            try:
                # TODO: find a more efficient variable-length blocking read?
                incoming = self.pyserial.read(size=1)
            except OSError as exc:
                message, port = "Serial read error", self.pyserial.port
                error = _exceptions.SerialIoFailed(message, port, exc)

            if incoming or error:
                with self.monitor:
                    self.incoming.extend(incoming)
                    self.exception = self.exception or error
                    self.monitor.notify_all()
                    self.resolve_futures()
            if self.exception:
                break

    def write_loop(self) -> None:
        log.debug("Starting thread")
        outgoing = b""
        while True:
            # Avoid blocking on writes if at all possible:
            # https://github.com/pyserial/pyserial/issues/280
            # https://github.com/pyserial/pyserial/issues/281
            error = None
            try:
                if outgoing:
                    self.pyserial.write(outgoing)
                bytes_written = len(outgoing)
                bytes_available = max(0, 256 - self.pyserial.out_waiting)
            except OSError as exc:
                bytes_written = 0
                bytes_available = 0
                message, port = "Serial write error", self.pyserial.port
                error = _exceptions.SerialIoFailed(message, port, exc)

            with self.monitor:
                if bytes_written or error:
                    del self.outgoing[:bytes_written]
                    self.exception = self.exception or error
                    self.monitor.notify_all()
                    self.resolve_futures()
                if self.exception:
                    break
                if bytes_available > 0:
                    outgoing = self.outgoing[:bytes_available]
                else:
                    self.monitor.wait(timeout=0.01)

    def create_future(self) -> asyncio.Future[None]:
        assert self.async_loop
        with self.monitor:
            future = self.async_loop.create_future()
            self.async_futures.append(future)
            return future

    def resolve_futures(self) -> None:
        def run_in_loop():
            with self.monitor:
                while self.async_futures:
                    self.async_futures.pop().set_result(None)

        if self.async_futures:
            assert self.async_loop
            self.async_loop.call_soon_threadsafe(run_in_loop)


def _deadline_from_timeout(timeout: float | None) -> float:
    if timeout is None or timeout >= threading.TIMEOUT_MAX:
        return threading.TIMEOUT_MAX
    elif timeout <= 0:
        return 0
    else:
        return min(threading.TIMEOUT_MAX, time.monotonic() + timeout)


def _timeout_from_deadline(deadline: float) -> float:
    if deadline >= threading.TIMEOUT_MAX:
        return threading.TIMEOUT_MAX
    elif deadline <= 0:
        return 0
    else:
        return max(0, deadline - time.monotonic())
