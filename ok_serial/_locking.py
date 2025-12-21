import contextlib
import fcntl
import logging
import os
import signal
import termios
from pathlib import Path
from typing import Literal

from ok_serial import _exceptions


SerialSharingType = Literal["oblivious", "polite", "exclusive", "stomp"]

log = logging.getLogger("ok_serial.locking")


@contextlib.contextmanager
def using_lock_file(device: str, sharing: SerialSharingType):
    parts = Path(device).parts[-2:]
    if parts[-1].isdigit() and parts[-2:][0].startswith("pt"):
        lock_path = Path(f"/var/lock/LCK..{'.'.join(parts[-2:])}")
    else:
        lock_path = Path(f"/var/lock/LCK..{parts[-1]}")
    for _try in range(10):
        if _try_lock_file(device=device, lock_path=lock_path, sharing=sharing):
            break
    else:
        message = "Serial port busy (retries exceeded)"
        raise _exceptions.SerialOpenBusy(message, device)

    yield

    _release_lock_file(lock_path, sharing)


@contextlib.contextmanager
def using_fd_lock(device: str, fd: int, sharing: SerialSharingType):
    try:
        if sharing == "polite":
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            log.debug("Acquired flock(LOCK_SH) on %s", device)
        elif sharing != "oblivious":
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            log.debug("Acquired flock(LOCK_EX) on %s", device)
    except BlockingIOError as ex:
        message = "Serial port busy (flock claimed)"
        raise _exceptions.SerialOpenBusy(message, device) from ex
    except OSError:
        log.warning("Can't lock (flock) %s", device, exc_info=True)

    try:
        if sharing in ("exclusive", "stomp"):
            fcntl.ioctl(fd, termios.TIOCEXCL)
            log.debug("Acquired TIOCEXCL on %s", device)
    except OSError:
        log.warning("Can't lock (TIOCEXCL) %s", device, exc_info=True)

    yield

    try:
        fcntl.ioctl(fd, termios.TIOCNXCL)
        log.debug("Released TIOCEXCL on %s", device)
    except OSError:
        log.warning("Can't release TIOCEXCL on %s", device, exc_info=True)

    try:
        if sharing != "oblivious":
            fcntl.flock(fd, fcntl.LOCK_UN | fcntl.LOCK_NB)
            log.debug("Released flock on %s", device)
    except OSError:
        log.warning("Can't release flock on %s", device, exc_info=True)


def _try_lock_file(
    *, device: str, lock_path: Path, sharing: SerialSharingType
) -> bool:
    if sharing == "oblivious":
        return True

    if not lock_path.parent.is_dir():
        log.debug("No lock directory %s", lock_path.parent)
        return True

    if owner_pid := _lock_file_owner(lock_path):
        if owner_pid == os.getpid():
            log.debug("We already own %s", lock_path)
            return True

        if sharing == "stomp":
            try:
                os.kill(owner_pid, signal.SIGTERM)
                log.debug("Killed owner %d of %s", owner_pid, lock_path)
            except OSError:
                log.warning(
                    "Can't kill owner %d of %s",
                    owner_pid,
                    lock_path,
                    exc_info=True,
                )
        else:
            log.debug("PID %d owns %s", owner_pid, lock_path)
            message = f"Serial port busy ({lock_path}: pid={owner_pid})"
            raise _exceptions.SerialOpenBusy(message, device)

    try:
        write_mode = "wt" if sharing == "stomp" else "xt"
        with lock_path.open(write_mode) as lock_file:
            lock_file.write(f"{os.getpid():>10d}\n")
    except FileExistsError:
        log.warning("Conflict creating %s", lock_path)
        return False  # try again (with a retry limit)
    except OSError:
        log.warning("Can't create %s", lock_path, exc_info=True)
        return True  # proceed anyway

    log.debug("Claimed %s", lock_path)
    return True


def _release_lock_file(lock_path: Path, sharing: SerialSharingType) -> None:
    if sharing == "oblivious" or _lock_file_owner(lock_path) != os.getpid():
        return

    try:
        lock_path.unlink()
        log.debug("Released %s", lock_path)
    except OSError:
        log.warning("Can't release %s", lock_path, exc_info=True)


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
            log.debug("Removed bad/stale %s", lock_path)
        except OSError:
            log.warning("Can't remove %s", lock_path, exc_info=True)
        return None
    except OSError:
        log.warning("Can't check %s", lock_path, exc_info=True)
        return None
