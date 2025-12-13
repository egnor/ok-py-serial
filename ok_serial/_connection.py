import asyncio
import contextlib
import errno
import logging
import serial
import threading
import time

from ok_serial import _locking

logger = logging.getLogger(__name__)


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
            self._io_threads: list[threading.Thread] = []
            self._io_condition = threading.Condition()
            self._io_incoming = bytearray()
            self._io_outgoing = bytearray()
            self._io_exception: None | OSError = None
            self._io_shutdown: bool = False

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._async_event = None
                self._async_notify = lambda: None
            else:
                self._async_event = aev = asyncio.Event()
                self._async_notify = lambda: loop.call_soon_threadsafe(aev.set)

            cleanup.enter_context(_locking.using_lock_file(port, sharing))

            logger.debug("Opening %s (%dbps, %s)", port, baud, sharing)
            try:
                self._pyserial = cleanup.enter_context(
                    serial.Serial(
                        port=port,
                        baudrate=baud,
                        write_timeout=0.1,
                    )
                )
            except OSError as exc:
                if exc.errno == errno.EBUSY:
                    message = f"{port} is busy (EBUSY)"
                    raise _locking.PortBusyException(message) from exc
                raise

            if hasattr(self._pyserial, "fileno"):
                fd = self._pyserial.fileno()
                cleanup.enter_context(_locking.using_fd_lock(port, fd, sharing))

            cleanup.callback(self._stop_io_threads)
            for t, n in ((self._reader, "reader"), (self._writer, "writer")):
                thread = threading.Thread(target=t, name=f"{self._port} {n}")
                thread.start()
                self._io_threads.append(thread)

            self._cleanup = cleanup.pop_all()

    def __del__(self) -> None:
        self._cleanup.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._cleanup.__exit__(exc_type, exc_value, traceback)

    def read_sync(self, timeout: float | None = None) -> bytes:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._io_condition:
                if self._io_incoming:
                    incoming = self._io_incoming[:]
                    self._io_incoming.clear()
                    self._io_condition.notify_all()
                    return incoming
                elif self._io_exception:
                    raise self._io_exception
                elif deadline is None:
                    wait_timeout = None
                else:
                    wait_timeout = deadline - time.monotonic()
                    if wait_timeout <= 0:
                        return b""
                self._io_condition.wait(timeout=wait_timeout)

    def write(self, data: bytes) -> None:
        with self._io_condition:
            if self._io_exception:
                raise self._io_exception
            else:
                self._io_outgoing.extend(data)
                self._io_condition.notify_all()

    def drain_sync(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._io_condition:
                if self._io_exception:
                    raise self._io_exception
                elif self._io_shutdown or not self._io_outgoing:
                    return
                elif deadline is None:
                    wait_timeout = None
                else:
                    wait_timeout = deadline - time.monotonic()
                    if wait_timeout <= 0:
                        return
                self._io_condition.wait(timeout=wait_timeout)

    def incoming_size(self) -> int:
        with self._io_condition:
            return len(self._io_incoming)

    def outgoing_size(self) -> int:
        with self._io_condition:
            return len(self._io_outgoing)

    def close(self) -> None:
        self._cleanup.close()

    def _reader(self) -> None:
        logger.debug("Reader starting")
        error = None
        while True:
            try:
                incoming = self._pyserial.read(size=1)
            except OSError as exc:
                error = exc

            with self._io_condition:
                if self._io_shutdown or self._io_exception:
                    break
                elif error:
                    self._io_exception = error
                    self._io_condition.notify_all()
                    self._async_notify()
                    break
                elif incoming:
                    self._io_incoming.extend(incoming)
                    self._io_condition.notify_all()
                    self._async_notify()

    def _writer(self) -> None:
        logger.debug("Writer starting")
        outgoing = b""
        error = None
        while True:
            # Avoid blocking on writes if at all possible:
            # https://github.com/pyserial/pyserial/issues/280
            # https://github.com/pyserial/pyserial/issues/281
            try:
                if outgoing:
                    self._pyserial.write(outgoing)
                    outgoing = b""
                chunk_max = max(0, 256 - self._pyserial.out_waiting)
            except OSError as exc:
                error = exc

            with self._io_condition:
                if self._io_shutdown or self._io_exception:
                    break
                elif error:
                    self._io_exception = error
                    self._io_condition.notify_all()
                    self._async_notify()
                    break
                elif chunk_max <= 0:
                    self._io_condition.wait(timeout=0.01)
                    continue
                elif self._io_outgoing:
                    outgoing = self._io_outgoing[:chunk_max]
                    self._io_outgoing[:chunk_max] = b""
                    self._io_condition.notify_all()
                    self._async_notify()
                else:
                    self._io_condition.wait()
                    continue

    def _stop_io_threads(self) -> None:
        with self._io_condition:
            self._io_shutdown = True
            self._io_condition.notify_all()

        try:
            self._pyserial.cancel_read()
            self._pyserial.cancel_write()
            logger.debug("Cancelled I/O on %s", self._port)
        except OSError:
            logger.warn("Can't cancel I/O on %s", self._port, exc_info=True)

        logger.debug("Joining I/O threads for %s", self._port)
        for thr in self._io_threads:
            thr.join()
