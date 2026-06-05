import array
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

# Linux TIOCGEXCL: _IOR('T', 0x40, int)
TIOCGEXCL = getattr(termios, "TIOCGEXCL", 0x80045440)

# Bits set on the port's termios as a "canary" to detect other users.
# All three are cleared by cfmakeraw() and by pyserial's reconfigure;
# all three are no-ops in the modes ok-serial uses.
_CANARY_IFLAG = termios.PARMRK | termios.IGNBRK
_CANARY_LFLAG = termios.ECHONL


class PortLock(contextlib.AbstractContextManager):
    """Holds all port-locking state for one `SerialConnection`.

    Context-manager scope covers the LCK..* (or .polite) lockfile. Call
    `attach_fd(fd)` after the device is open to claim fd-level state
    (flock, TIOCEXCL, termios canary). Call `check()` periodically.
    """

    def __init__(self, device: str, sharing: SerialSharingType) -> None:
        self.device = device
        self.sharing = sharing

        dev_parts = Path(device).parts[-2:]
        if dev_parts[-1].isdigit() and dev_parts[0].startswith("pt"):
            self._lock_path = Path(f"/var/lock/LCK..{'.'.join(dev_parts)}")
        else:
            self._lock_path = Path(f"/var/lock/LCK..{dev_parts[-1]}")

        self._polite_path = Path(str(self._lock_path) + ".polite")
        self._fd: int | None = None

    def __enter__(self) -> "PortLock":
        if self.sharing == "polite":
            if owner := _lock_file_owner(self._lock_path):
                message = f"Serial port busy ({self._lock_path}: pid={owner})"
                raise _exceptions.SerialOpenBusy(message, self.device)

            # use exclusive semantics between polite users
            _claim_lock_file(self.device, self._polite_path, "exclusive")

        if self.sharing in ("exclusive", "stomp"):
            _claim_lock_file(self.device, self._lock_path, self.sharing)

        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        # remove any lock file credited to this process
        for del_path in (self._polite_path, self._lock_path):
            if _lock_file_owner(del_path) == os.getpid():
                try:
                    del_path.unlink()
                    log.debug("Removed %s", del_path)
                except OSError as ex:
                    log.warning("Removing %s: %s", del_path, ex)

    def attach_fd(self, fd: int) -> None:
        """Claim fd-level locking."""
        self._fd = fd

        # flock locking - probe and release for "polite", otherwise claim
        try:
            if self.sharing == "polite":
                # Probe for any existing flock holder, then release so we
                # don't shut out a future exclusive user.
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN | fcntl.LOCK_NB)
                log.debug("Politely checked flock on %s", self.device)
            if self.sharing in ("exclusive", "stomp"):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                log.debug("Acquired flock(LOCK_EX) on %s", self.device)
        except BlockingIOError as ex:
            message = "Serial port busy (flock claimed)"
            raise _exceptions.SerialOpenBusy(message, self.device) from ex
        except OSError as ex:
            log.warning("Locking (flock) %s: %s", self.device, ex)

        if self.sharing == "polite":
            try:
                modified = list(termios.tcgetattr(fd))
                modified[0] = modified[0] | _CANARY_IFLAG
                modified[3] = modified[3] | _CANARY_LFLAG
                termios.tcsetattr(fd, termios.TCSANOW, modified)
                log.debug("Installed conflict monitor on %s", self.device)
            except (OSError, termios.error) as ex:
                msg = "Monitoring %s: %s"
                log.warning(msg, self.device, ex)

        if self.sharing in ("exclusive", "stomp"):
            try:
                fcntl.ioctl(fd, termios.TIOCEXCL)
                log.debug("Acquired TIOCEXCL on %s", self.device)
            except OSError as ex:
                message, dev = "Locking (TIOCEXCL) %s: %s", self.device
                log.warning(message, dev, ex)

    def release_fd(self) -> None:
        """Release fd-level locking.

        Closing the fd does this also, EXCEPT if a pty master remains open,
        TIOCEXCL stays set, and that happens to be the case in testing.
        """

        if self._fd is None:
            return

        if self.sharing in ("exclusive", "stomp"):
            try:
                fcntl.ioctl(self._fd, termios.TIOCNXCL)
                log.debug("Released TIOCEXCL on %s", self.device)
            except OSError as ex:
                message = "Releasing (TIOCNXCL) %s: %s"
                log.warning(message, self.device, ex)

    def check(self) -> None:
        """Raises SerialIoConflict in "polite" mode if outside use was seen."""

        if self.sharing == "polite" and self._fd is not None:
            try:
                termios_attr = termios.tcgetattr(self._fd)
                excl_state = array.array("i", [0])
                fcntl.ioctl(self._fd, TIOCGEXCL, excl_state, True)
            except (OSError, termios.error):
                message = "Error checking for conflict"
                raise _exceptions.SerialIoException(message, self.device)

            iflag_ok = (termios_attr[0] & _CANARY_IFLAG) == _CANARY_IFLAG
            lflag_ok = (termios_attr[3] & _CANARY_LFLAG) == _CANARY_LFLAG
            if not iflag_ok or not lflag_ok:
                message = "Port conflict detected (initialized)"
                raise _exceptions.SerialIoConflict(message, self.device)

            if excl_state[0]:
                message = "Port conflict detected (TIOCEXCL set)"
                raise _exceptions.SerialIoConflict(message, self.device)

        if self.sharing == "polite" and self._lock_path.exists():
            if owner := _lock_file_owner(self._lock_path):
                message = (
                    f"Port conflict detected ({self._lock_path} pid={owner})"
                )
            else:
                message = f"Port conflict detected ({self._lock_path} appeared)"
            raise _exceptions.SerialIoConflict(message, self.device)


def _claim_lock_file(device: str, lock_path: Path, mode: str) -> None:
    for _try in range(10):
        if not lock_path.parent.is_dir():
            log.debug("No lock directory %s", lock_path.parent)
            return

        if owner_pid := _lock_file_owner(lock_path):
            if owner_pid == os.getpid():
                log.debug("We already own %s", lock_path)
                return
            if mode == "stomp":
                try:
                    os.kill(owner_pid, signal.SIGTERM)
                    log.debug("Killed owner %d of %s", owner_pid, lock_path)
                except OSError as ex:
                    msg = "Killing owner %d of %s: %s"
                    log.warning(msg, owner_pid, lock_path, ex)
            else:
                log.debug("PID %d owns %s", owner_pid, lock_path)
                message = f"Serial port busy ({lock_path}: pid={owner_pid})"
                raise _exceptions.SerialOpenBusy(message, device)

        try:
            write_mode = "wt" if mode == "stomp" else "xt"
            with lock_path.open(write_mode) as lock_file:
                lock_file.write(f"{os.getpid():>10d}\n")
        except FileExistsError:
            log.warning("Conflict creating %s", lock_path)
            continue  # retry
        except OSError as ex:
            log.warning("Creating %s: %s", lock_path, ex)
            return  # proceed anyway

        log.debug("Claimed %s", lock_path)
        return

    message = "Serial port busy (retries exceeded)"
    raise _exceptions.SerialOpenBusy(message, device)


def _lock_file_owner(lock_path: Path) -> int | None:
    try:
        with lock_path.open("rt") as lock_file:
            owner_pid = int(lock_file.read(128).strip())
        os.kill(owner_pid, 0)  # check if process exists
        return owner_pid
    except FileNotFoundError:
        return None
    except PermissionError:
        return owner_pid  # exists but not allowed to kill even with signal 0
    except (ProcessLookupError, ValueError):
        try:
            lock_path.unlink()
            log.debug("Removed bad/stale %s", lock_path)
        except OSError as ex:
            log.warning("Removing %s: %s", lock_path, ex)
        return None
    except OSError as ex:
        log.warning("Checking %s: %s", lock_path, ex)
        return None
