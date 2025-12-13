import contextlib
import errno
import logging
import serial
import threading

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
            self._shutdown_event = threading.Event()
            self._io_threads: list[threading.Thread] = []
            self._io_condition = threading.Condition()
            self._io_incoming = bytearray()
            self._io_outgoing = bytearray()
            self._io_error: None | Exception = None
            self._io_shutdown: bool = False

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

            cleanup.callback(self._stop_io)
            for t, n in ((self._do_read, "reader"), (self._do_write, "writer")):
                thread = threading.Thread(target=t, name=f"{self._port} {n}")
                thread.start()
                self._io_threads.append(thread)

            self._cleanup = cleanup.pop_all()

    def __del__(self) -> None:
        self._cleanup.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._cleanup.__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        self._cleanup.close()

    def _do_read(self) -> None:
        logger.debug("Reader starting")
        while True:
            try:
                incoming = self._pyserial.read(size=1)
            except OSError as exc:
                self._io_read_error = exc
                break

            with self._io_condition:
                if self._io_shutdown:
                    break
                elif incoming:
                    self._io_incoming.extend(incoming)

    def _do_write(self) -> None:
        logger.debug("Writer starting")
        while True:
            outgoing: bytes | None = None
            with self._io_condition:
                if self._io_shutdown:
                    break
                elif self._io_outgoing:
                    outgoing = bytes(self._io_outgoing)
                    self._io_outgoing.clear()
                else:
                    self._io_condition.wait()
                    continue

            # See:
            # https://github.com/pyserial/pyserial/issues/280
            # https://github.com/pyserial/pyserial/issues/281
            try:
                self._pyserial.write(outgoing)
            except OSError as exc:
                self._io_write_error = exc
                break

    def _stop_io(self) -> None:
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
        [thr.join() for thr in self._threads]
