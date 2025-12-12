import contextlib
import fcntl
import logging
import os
import signal
import termios
from pathlib import Path
from typing import Literal


SharingType = Literal["oblivious", "polite", "exclusive", "stomp"]

logger = logging.getLogger(__name__)


class PortBusyException(OSError):
    def __init__(self, message: str):
        super().__init__(message)


@contextlib.contextmanager
def using_lock_file(port: str, sharing: SharingType):
    lock_path = Path("/var/lock") / f"LCK..{Path(port).name}"
    for _try in range(10):
        if _try_lock_file(port=port, lock_path=lock_path, sharing=sharing):
            break
    else:
        raise PortBusyException(f"{port} is busy (contention retries exceeded)")

    yield

    _release_lock_file(lock_path, sharing)


@contextlib.contextmanager
def using_fd_lock(port: str, fd: int, sharing: SharingType):
    try:
        if sharing == "polite":
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            logger.debug("Acquired flock(LOCK_SH) on %s", port)
        elif sharing != "oblivious":
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            logger.debug("Acquired flock(LOCK_EX) on %s", port)
    except BlockingIOError as e:
        raise PortBusyException(f"{port} is busy (flock)") from e
    except OSError:
        logger.warn("Can't lock (flock) %s", port, exc_info=True)

    try:
        if sharing in ("exclusive", "stomp"):
            fcntl.ioctl(fd, termios.TIOCEXCL)
            logger.debug("Acquired TIOCEXCL on %s", port)
    except OSError:
        logger.warn("Can't lock (TIOCEXCL) %s", port, exc_info=True)

    yield

    try:
        fcntl.ioctl(fd, termios.TIOCNXCL)
        logger.debug("Released TIOCEXCL on %s", port)
    except OSError:
        logger.warn("Can't release TIOCEXCL on %s", port, exc_info=True)

    try:
        if sharing != "oblivious":
            fcntl.flock(fd, fcntl.LOCK_UN | fcntl.LOCK_NB)
            logger.debug("Released flock on %s", port)
    except OSError:
        logger.warn("Can't release flock on %s", port, exc_info=True)


def _try_lock_file(*, port: str, lock_path: Path, sharing: SharingType) -> bool:
    if sharing == "oblivious":
        return True

    if not lock_path.parent.is_dir():
        logger.debug("No lock directory %s", lock_path.parent)
        return True

    if owner_pid := _lock_file_owner(lock_path):
        if owner_pid == os.getpid():
            logger.debug("We already own %s", lock_path)
            return True

        if sharing == "stomp":
            try:
                os.kill(owner_pid, signal.SIGTERM)
                logger.debug("Killed owner %d of %s", owner_pid, lock_path)
            except OSError:
                logger.warn(
                    "Can't kill owner %d of %s",
                    owner_pid,
                    lock_path,
                    exc_info=True,
                )
        else:
            logger.debug("PID %d owns %s", owner_pid, lock_path)
            raise PortBusyException(
                f"{port} is busy ({lock_path}: pid={owner_pid})"
            )

    try:
        write_mode = "wt" if sharing == "stomp" else "xt"
        with lock_path.open(write_mode) as lock_file:
            lock_file.write(f"{os.getpid():>10d}\n")
    except FileExistsError:
        logger.warn("Conflict creating %s", lock_path)
        return False  # try again (with a retry limit)
    except OSError:
        logger.warn("Can't create %s", lock_path, exc_info=True)
        return True  # proceed anyway

    logger.debug("Claimed %s", lock_path)
    return True


def _release_lock_file(lock_path: Path, sharing: SharingType) -> None:
    if sharing == "oblivious" or _lock_file_owner(lock_path) != os.getpid():
        return

    try:
        lock_path.unlink()
        logger.debug("Released %s", lock_path)
    except OSError:
        logger.warn("Can't release %s", lock_path, exc_info=True)


def _lock_file_owner(lock_path: Path) -> int | None:
    try:
        with lock_path.open("rt") as lock_file:
            owner_pid = int(lock_file.read(128).strip())
        os.kill(owner_pid, 0)  # check if process exists
        return owner_pid
    except FileNotFoundError:
        return None
    except (ProcessLookupError, ValueError):
        try:
            lock_path.unlink()
            logger.debug("Removed bad/stale %s", lock_path)
        except OSError:
            logger.warn("Can't remove %s", lock_path, exc_info=True)
        return None
    except OSError:
        logger.warn("Can't check %s", lock_path, exc_info=True)
        return None
