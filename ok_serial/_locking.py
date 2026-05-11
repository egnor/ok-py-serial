import contextlib
import fcntl
import logging
import os
import signal
import termios
from pathlib import Path
from typing import Literal

from ok_serial._exceptions import SerialOpenBusy


SerialSharingType = Literal["oblivious", "polite", "exclusive", "stomp"]

log = logging.getLogger("ok_serial.locking")

# Linux TIOCGEXCL: _IOR('T', 0x40, int)
TIOCGEXCL = getattr(termios, "TIOCGEXCL", 0x80045440)

# Bits set on the port's termios as a "canary" in polite mode. All three are
# cleared by cfmakeraw() and by pyserial's reconfigure; all three are no-ops
# in the modes ok-serial uses (PARMRK only matters with PARENB; IGNBRK only
# affects BREAK delivery, which we don't surface; ECHONL only matters with
# ICANON). If any of these clears, someone else reconfigured the port.
_CANARY_IFLAG = termios.PARMRK | termios.IGNBRK
_CANARY_LFLAG = termios.ECHONL


def _lock_path_for(device: str) -> Path:
    parts = Path(device).parts[-2:]
    if parts[-1].isdigit() and parts[0].startswith("pt"):
        return Path(f"/var/lock/LCK..{'.'.join(parts)}")
    return Path(f"/var/lock/LCK..{parts[-1]}")


class PortLock(contextlib.AbstractContextManager):
    """Holds all port-locking state for one `SerialConnection`.

    Context-manager scope covers the LCK..* (or .polite) lockfile. Call
    `attach_fd(fd)` after the device is open to claim fd-level state
    (flock, TIOCEXCL, termios canary). Call `check()` periodically.
    """

    fd: int | None
    _created_path: Path | None
    _canary_baseline: list | None

    def __init__(self, device: str, sharing: SerialSharingType) -> None:
        self.device = device
        self.sharing = sharing
        self.path = _lock_path_for(device)
        self.polite_path = self.path.with_name(self.path.name + ".polite")
        self.fd = None
        self._created_path = None
        self._canary_baseline = None
        self._tiocexcl_held = False

    def __enter__(self) -> "PortLock":
        self._claim_lockfile()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._created_path is not None:
            _release_lock_file(self._created_path)
            self._created_path = None

    def attach_fd(self, fd: int) -> None:
        """Claim fd-level state."""
        self.fd = fd
        if self.sharing == "oblivious":
            return

        try:
            if self.sharing == "polite":
                # Probe for any existing flock holder, then release so we
                # don't shut out a future exclusive user.
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN | fcntl.LOCK_NB)
                log.debug("Probed flock on %s (polite)", self.device)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                log.debug("Acquired flock(LOCK_EX) on %s", self.device)
        except BlockingIOError as ex:
            message = "Serial port busy (flock claimed)"
            raise SerialOpenBusy(message, self.device) from ex
        except OSError:
            log.warning("Can't lock (flock) %s", self.device, exc_info=True)

        if self.sharing in ("exclusive", "stomp"):
            try:
                fcntl.ioctl(fd, termios.TIOCEXCL)
                self._tiocexcl_held = True
                log.debug("Acquired TIOCEXCL on %s", self.device)
            except OSError:
                msg, dev = "Can't lock (TIOCEXCL) %s", self.device
                log.warning(msg, dev, exc_info=True)

        if self.sharing == "polite":
            self._install_canary(fd)

    def check(self) -> str | None:
        """Returns a reason string if polite mode should cede, else None."""
        if self.sharing != "polite" or self.fd is None:
            return None

        if self._canary_baseline is not None:
            try:
                current = termios.tcgetattr(self.fd)
                iflag_ok = (current[0] & _CANARY_IFLAG) == _CANARY_IFLAG
                lflag_ok = (current[3] & _CANARY_LFLAG) == _CANARY_LFLAG
                if not iflag_ok or not lflag_ok:
                    return "termios canary cleared"
            except (OSError, termios.error):
                pass

        try:
            import array

            buf = array.array("i", [0])
            fcntl.ioctl(self.fd, TIOCGEXCL, buf, True)
            if buf[0]:
                return "TIOCEXCL set by another user"
        except OSError:
            pass

        owner = _lock_file_owner(self.path)
        if owner and owner != os.getpid():
            return f"{self.path} appeared (pid={owner})"
        if owner is None and self.path.exists():
            return f"{self.path} appeared"

        return None

    def _claim_lockfile(self) -> None:
        if self.sharing == "oblivious":
            return

        if self.sharing == "polite":
            owner = _lock_file_owner(self.path)  # cleans up stale
            if self.path.exists():
                message = f"Serial port busy ({self.path}"
                message += f": pid={owner})" if owner else ")"
                raise SerialOpenBusy(message, self.device)
            self._try_claim(self.polite_path, mode="exclusive")
        else:
            self._try_claim(self.path, mode=self.sharing)

    def _try_claim(self, path: Path, *, mode: str) -> None:
        for _try in range(10):
            if not path.parent.is_dir():
                log.debug("No lock directory %s", path.parent)
                return

            if owner_pid := _lock_file_owner(path):
                if owner_pid == os.getpid():
                    log.debug("We already own %s", path)
                    return
                if mode == "stomp":
                    try:
                        os.kill(owner_pid, signal.SIGTERM)
                        log.debug("Killed owner %d of %s", owner_pid, path)
                    except OSError:
                        msg = "Can't kill owner %d of %s"
                        log.warning(msg, owner_pid, path, exc_info=True)
                else:
                    log.debug("PID %d owns %s", owner_pid, path)
                    message = f"Serial port busy ({path}: pid={owner_pid})"
                    raise SerialOpenBusy(message, self.device)

            try:
                write_mode = "wt" if mode == "stomp" else "xt"
                with path.open(write_mode) as lock_file:
                    lock_file.write(f"{os.getpid():>10d}\n")
            except FileExistsError:
                log.warning("Conflict creating %s", path)
                continue  # retry
            except OSError:
                log.warning("Can't create %s", path, exc_info=True)
                return  # proceed anyway

            log.debug("Claimed %s", path)
            self._created_path = path
            return

        message = "Serial port busy (retries exceeded)"
        raise SerialOpenBusy(message, self.device)

    def _install_canary(self, fd: int) -> None:
        try:
            baseline = termios.tcgetattr(fd)
            modified = list(baseline)
            modified[0] = modified[0] | _CANARY_IFLAG
            modified[3] = modified[3] | _CANARY_LFLAG
            termios.tcsetattr(fd, termios.TCSANOW, modified)
            self._canary_baseline = baseline
            log.debug("Installed polite canary on %s", self.device)
        except (OSError, termios.error):
            msg = "Can't install polite canary on %s"
            log.warning(msg, self.device, exc_info=True)


def _release_lock_file(lock_path: Path) -> None:
    if _lock_file_owner(lock_path) != os.getpid():
        return

    try:
        lock_path.unlink()
        log.debug("Removed %s", lock_path)
    except OSError:
        log.warning("Can't remove %s", lock_path, exc_info=True)


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
