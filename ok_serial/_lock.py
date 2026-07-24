import array
import contextlib
import errno
import fcntl
import logging
import os
import signal
import termios
import time
from pathlib import Path
from typing import Literal

from ok_serial import _exceptions


SerialSharingType = Literal["oblivious", "polite", "exclusive", "stomp"]

log = logging.getLogger("ok_serial.lock")

# Linux TIOCGEXCL: _IOR('T', 0x40, int)
_TIOCGEXCL = getattr(termios, "TIOCGEXCL", 0x80045440)

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

        self._fd: int | None = None
        self._linux_pty_quirk = device.startswith("/dev/pts/")
        self._polite_path = Path(str(self._lock_path) + ".polite")

    def __enter__(self) -> "PortLock":
        if self.sharing == "polite":
            if owner := _lock_file_owner(self._lock_path):
                msg = f"Port busy ({self._lock_path} pid={owner})"
                raise _exceptions.SerialOpenBusy(msg, self.device)

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
            msg = "Port busy (flock claimed)"
            raise _exceptions.SerialOpenBusy(msg, self.device) from ex
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
                log.warning("Monitoring %s: %s", self.device, ex)

        # skip TIOCEXCL on Linux pty slaves since it stays until *master* close
        if self.sharing in ("exclusive", "stomp"):
            try:
                if self._linux_pty_quirk:
                    log.debug("Skipping TIOCEXCL on pty %s", self.device)
                else:
                    fcntl.ioctl(fd, termios.TIOCEXCL)
                    log.debug("Acquired TIOCEXCL on %s", self.device)
            except OSError as ex:
                msg, dev = "Locking (TIOCEXCL) %s: %s", self.device
                log.warning(msg, dev, ex)

    def release_fd(self) -> None:
        """Release fd-level locking."""

        if self._fd is None:
            return

        # This also happens on last-close, but release explicitly anyway
        if self.sharing in ("exclusive", "stomp") and not self._linux_pty_quirk:
            try:
                fcntl.ioctl(self._fd, termios.TIOCNXCL)
                log.debug("Released TIOCEXCL on %s", self.device)
            except OSError as ex:  # expected if the device is gone
                log.debug("Releasing (TIOCNXCL) %s: %s", self.device, ex)

    def check(self) -> None:
        """Raises SerialIoConflict in "polite" mode if outside use was seen."""

        if self.sharing == "polite" and self._fd is not None:
            try:
                termios_attr = termios.tcgetattr(self._fd)
                excl_state = array.array("i", [0])
                fcntl.ioctl(self._fd, _TIOCGEXCL, excl_state, True)
            except (OSError, termios.error):
                msg = "Error checking for conflict"
                raise _exceptions.SerialIoException(msg, self.device)

            iflag_ok = (termios_attr[0] & _CANARY_IFLAG) == _CANARY_IFLAG
            lflag_ok = (termios_attr[3] & _CANARY_LFLAG) == _CANARY_LFLAG
            if not iflag_ok or not lflag_ok:
                msg = "Port conflict detected (termios reset)"
                raise _exceptions.SerialIoConflict(msg, self.device)

            if excl_state[0]:
                msg = "Port conflict detected (TIOCEXCL seen)"
                raise _exceptions.SerialIoConflict(msg, self.device)

        if self.sharing == "polite" and self._lock_path.exists():
            if owner := _lock_file_owner(self._lock_path):
                msg = f"Port conflict detected ({self._lock_path} pid={owner})"
            else:
                msg = f"Port conflict detected ({self._lock_path})"
            raise _exceptions.SerialIoConflict(msg, self.device)


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
                for _try in range(5):
                    try:
                        os.kill(owner_pid, signal.SIGTERM)
                    except OSError as ex:
                        if ex.errno != errno.ESRCH:
                            msg = "Killing pid=%d (%s): %s"
                            log.warning(msg, owner_pid, lock_path, ex)
                        break
                    else:
                        log.warning("Killed pid=%d (%s)", owner_pid, lock_path)
                        time.sleep(0.1)  # wait to verify/retry
            else:
                log.debug("PID %d owns %s", owner_pid, lock_path)
                msg = f"Port busy ({lock_path} pid={owner_pid})"
                raise _exceptions.SerialOpenBusy(msg, device)

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

    raise _exceptions.SerialOpenBusy("Port busy (retries exceeded)", device)


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
