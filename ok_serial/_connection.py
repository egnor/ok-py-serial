import contextlib
import errno
import logging
import serial

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

            cleanup.enter_context(_locking.using_lock_file(port, sharing))

            # See:
            # https://github.com/pyserial/pyserial/issues/280
            # https://github.com/pyserial/pyserial/issues/281
            logger.debug("Opening %s (%dbps, %s)", port, baud, sharing)
            try:
                self._pyserial = cleanup.enter_context(
                    serial.Serial(
                        port=port,
                        baudrate=baud,
                        timeout=0.0,
                        write_timeout=0.1,
                    )
                )
            except OSError as e:
                if e.errno == errno.EBUSY:
                    message = f"{port} is busy (EBUSY)"
                    raise _locking.PortBusyException(message) from e
                raise

            if hasattr(self._pyserial, "fileno"):
                fd = self._pyserial.fileno()
                cleanup.enter_context(_locking.using_fd_lock(port, fd, sharing))

            self._cleanup = cleanup.pop_all()

    def __del__(self) -> None:
        self._cleanup.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._cleanup.__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        self._cleanup.close()
