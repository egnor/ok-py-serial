import fcntl
import logging
import os
import serial
import signal
from pathlib import Path
from typing import Literal


SharingType = Literal["oblivious", "polite", "exclusive", "takeover"]

logger = logging.getLogger(__name__)


class SerialPortBusyException(OSError):
    def __init__(self, port: str):
        super().__init__(f"Serial port {port} is busy")
        self.filename = port


class SerialConnection:
    def __init__(
        self, port: str, *, baud: int = 115200, sharing: SharingType = "polite"
    ):
        # Lock file (/var/lock/LCK..<portname>) acquisition
        self._port = port
        self._sharing = sharing
        self._lock_path = Path("/var/lock") / f"LCK..{Path(self._port).name}"
        for _try in range(10):
            if self._try_acquire_lock_file(sharing=sharing):
                break

        # See:
        # https://github.com/pyserial/pyserial/issues/280
        # https://github.com/pyserial/pyserial/issues/281
        self._pyserial = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=0.0,
            write_timeout=0.1,
        )

        if hasattr(self._pyserial, "fileno"):
            fd = self._pyserial.fileno()
            try:
                if sharing == "exclusive":
                    # acquire an exclusive lock, or fail
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif sharing == "polite":
                    # fail if a lock is held, but don't hold the lock
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN | fcntl.LOCK_NB)
            except OSError:
                pass

    def __enter__(self) -> "SerialConnection":
        self._pyserial.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._pyserial.close()
        except (OSError, serial.SerialException):
            pass

        self._release_lock_file()

    def _lock_file_owner(self) -> int | None:
        try:
            with self._lock_path.open("rt") as lock_file:
                owner_pid = int(lock_file.read(128).strip())
            os.kill(owner_pid, 0)  # check if process exists
            return owner_pid
        except FileNotFoundError:
            return None
        except (ProcessLookupError, ValueError):
            try:
                self._lock_path.unlink()
                logger.debug("Removed bad/stale %s", self._lock_path)
            except OSError:
                logger.warn("Can't delete %s", self._lock_path, exc_info=True)
            return None
        except OSError:
            logger.warn("Can't check %s", self._lock_path, exc_info=True)
            return None

    def _try_acquire_lock_file(self, *, sharing: SharingType) -> bool:
        if sharing == "oblivious" or not self._lock_path.parent.is_dir():
            return True

        if owner_pid := self._lock_file_owner():
            if owner_pid == os.getpid():
                logger.debug("We already own %s", self._lock_path)
                return True
            elif sharing == "takeover":
                try:
                    os.kill(owner_pid, signal.SIGTERM)
                    logger.debug(
                        "Killed owner %d of %s", owner_pid, self._lock_path
                    )
                except OSError:
                    logger.warn(
                        "Can't kill owner %d of %s",
                        owner_pid,
                        self._lock_path,
                        exc_info=True,
                    )
            else:
                logger.debug("PID %d owns %s", owner_pid, self._lock_path)
                raise SerialPortBusyException(self._port)

        try:
            write_mode = "wt" if sharing == "takeover" else "xt"
            with self._lock_path.open(write_mode) as lock_file:
                lock_file.write(f"{os.getpid():>10d}\n")
        except FileExistsError:
            logger.warn("Conflict creating %s", self._lock_path)
            return False  # try again in case of race (with retry limit)
        except OSError:
            logger.warn("Can't create %s", self._lock_path, exc_info=True)
            return True  # proceed anyway

        logger.debug("Claimed %s", self._lock_path)
        return True

    def _release_lock_file(self) -> None:
        try:
            if self._lock_file_owner() == os.getpid():
                self._lock_path.unlink()
                logger.debug("Released %s", self._lock_path)
        except OSError:
            logger.warn("Can't release %s", self._lock_path, exc_info=True)
