import asyncio
import contextlib
import errno
import logging
import serial
import threading
import time
from typing import Any, Callable

from ok_serial import _locking

log = logging.getLogger(__name__)


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
                self._async_event = aev = asyncio.Event()
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._async_notify: Callable[[], Any] = lambda: None
            else:
                self._async_notify = lambda: loop.call_soon_threadsafe(aev.set)

            cleanup.enter_context(_locking.using_lock_file(port, sharing))

            log.debug("Opening %s (%dbps, %s)", port, baud, sharing)
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

    def read_sync(
        self,
        *,
        min_read: int = 0,
        max_read: int = 65536,
        timeout: float | None = None,
    ) -> bytes:
        deadline = _deadline_from_timeout(timeout)
        while True:
            with self._io_condition:
                if len(self._io_incoming) >= min_read:
                    incoming = self._io_incoming[:max_read]
                    del self._io_incoming[:max_read]
                    return incoming
                elif self._io_exception:
                    raise self._io_exception
                else:
                    wait_timeout = _timeout_from_deadline(deadline)
                    if wait_timeout <= 0:
                        return b""
                    self._io_condition.wait(timeout=wait_timeout)

    async def read_async(
        self,
        *,
        min_read: int = 0,
        max_read: int = 65536,
    ) -> bytes:
        while True:
            if incoming := self.read_sync(
                min_read=min_read, max_read=max_read, timeout=0
            ):
                return incoming
            await self._async_event.wait()
            ### XXX TODO - how does _async_event get reset??

    def write(self, data: bytes) -> None:
        with self._io_condition:
            if self._io_exception:
                raise self._io_exception
            else:
                self._io_outgoing.extend(data)
                self._io_condition.notify_all()

    def drain_sync(
        self, *, max_level: int = 0, timeout: float | None = None
    ) -> None:
        deadline = _deadline_from_timeout(timeout)
        while True:
            with self._io_condition:
                if self._io_exception:
                    raise self._io_exception
                elif self._io_shutdown or len(self._io_outgoing) <= max_level:
                    return
                else:
                    wait_timeout = _timeout_from_deadline(deadline)
                    self._io_condition.wait(timeout=wait_timeout)

    async def drain_async(self, *, max_level: int = 0) -> None:
        while True:
            with self._io_condition:
                if self._io_exception:
                    raise self._io_exception

    def incoming_size(self) -> int:
        with self._io_condition:
            return len(self._io_incoming)

    def outgoing_size(self) -> int:
        with self._io_condition:
            return len(self._io_outgoing)

    def close(self) -> None:
        self._cleanup.close()

    def _reader(self) -> None:
        log.debug("Reader starting")
        while True:
            try:
                incoming = self._pyserial.read(size=1)
                error = None
            except OSError as exc:
                incoming = b""
                error = exc

            with self._io_condition:
                if incoming or error:
                    self._io_incoming.extend(incoming)
                    self._io_exception = self._io_exception or error
                    self._io_condition.notify_all()
                    self._async_notify()
                if self._io_shutdown or self._io_exception:
                    break

    def _writer(self) -> None:
        log.debug("Writer starting")
        outgoing = b""
        while True:
            # Avoid blocking on writes if at all possible:
            # https://github.com/pyserial/pyserial/issues/280
            # https://github.com/pyserial/pyserial/issues/281
            try:
                if outgoing:
                    self._pyserial.write(outgoing)
                last_write_size = len(outgoing)
                next_write_max = max(0, 256 - self._pyserial.out_waiting)
                error = None
            except OSError as exc:
                last_write_size = 0
                next_write_max = 0
                error = exc

            with self._io_condition:
                if last_write_size or error:
                    del self._io_outgoing[:last_write_size]
                    self._io_exception = self._io_exception or error
                    self._io_condition.notify_all()
                    self._async_notify()
                if self._io_shutdown or self._io_exception:
                    break
                if next_write_max > 0:
                    outgoing = self._io_outgoing[:next_write_max]
                else:
                    self._io_condition.wait(timeout=0.01)

    def _stop_io_threads(self) -> None:
        with self._io_condition:
            self._io_shutdown = True
            self._io_condition.notify_all()

        try:
            self._pyserial.cancel_read()
            self._pyserial.cancel_write()
            log.debug("Cancelled I/O on %s", self._port)
        except OSError:
            log.warn("Can't cancel I/O on %s", self._port, exc_info=True)

        log.debug("Joining I/O threads for %s", self._port)
        for thr in self._io_threads:
            thr.join()


def _deadline_from_timeout(timeout: float | None) -> float:
    if timeout is None or timeout >= threading.TIMEOUT_MAX:
        return threading.TIMEOUT_MAX
    elif timeout <= 0:
        return 0
    else:
        return time.monotonic() + timeout


def _timeout_from_deadline(deadline: float) -> float:
    if deadline >= threading.TIMEOUT_MAX:
        return threading.TIMEOUT_MAX
    elif deadline <= 0:
        return 0
    else:
        return max(0, deadline - time.monotonic())
