import os
from pathlib import Path

import pytest
import signal

import ok_serial
from ok_serial import _lock


#
# Lock file ownership
#


def test_lock_file_owner_returns_pid(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..test")
    lock_path.write_text(f"{os.getpid():>10d}\n")

    assert _lock._lock_file_owner(lock_path) == os.getpid()


def test_lock_file_owner_returns_none_for_missing_file(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..test")

    assert _lock._lock_file_owner(lock_path) is None


def test_lock_file_owner_removes_stale_lock(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..test")
    lock_path.write_text("999999999\n")  # Non-existent PID

    assert _lock._lock_file_owner(lock_path) is None
    assert not lock_path.exists()


def test_lock_file_owner_removes_invalid_content(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..test")
    lock_path.write_text("not a number\n")

    assert _lock._lock_file_owner(lock_path) is None
    assert not lock_path.exists()


#
# acquire() — lockfile semantics by sharing mode
#


def test_oblivious_skips_lock_file(fs):
    fs.create_dir("/var/lock")
    with _lock.PortLock("/dev/ttyTEST0", sharing="oblivious"):
        assert not Path("/var/lock/LCK..ttyTEST0").exists()


def test_polite_creates_sidecar_only(fs):
    fs.create_dir("/var/lock")
    with _lock.PortLock("/dev/ttyTEST0", sharing="polite"):
        # Polite does NOT create the standard LCK..* file (would shut out
        # other lock-aware programs); it uses a .polite sidecar instead.
        assert not Path("/var/lock/LCK..ttyTEST0").exists()
        polite_path = Path("/var/lock/LCK..ttyTEST0.polite")
        assert polite_path.exists()
        assert int(polite_path.read_text().strip()) == os.getpid()


def test_polite_fails_when_lock_file_held(fs):
    fs.create_dir("/var/lock")
    Path("/var/lock/LCK..ttyTEST0").write_text("         1\n")  # init
    with pytest.raises(ok_serial.SerialOpenBusy):
        with _lock.PortLock("/dev/ttyTEST0", sharing="polite"):
            pass


def test_polite_fails_when_other_polite_present(fs):
    fs.create_dir("/var/lock")
    Path("/var/lock/LCK..ttyTEST0.polite").write_text("         1\n")
    with pytest.raises(ok_serial.SerialOpenBusy):
        with _lock.PortLock("/dev/ttyTEST0", sharing="polite"):
            pass


def test_exclusive_creates_lock_file(fs):
    fs.create_dir("/var/lock")
    with _lock.PortLock("/dev/ttyTEST0", sharing="exclusive"):
        lock_path = Path("/var/lock/LCK..ttyTEST0")
        assert lock_path.exists()
        assert int(lock_path.read_text().strip()) == os.getpid()

    # check name variants
    with _lock.PortLock("/dev/subdir/ttyTEST1", sharing="exclusive"):
        assert Path("/var/lock/LCK..ttyTEST1").exists()
    with _lock.PortLock("/dev/pts/2", sharing="exclusive"):
        assert Path("/var/lock/LCK..pts.2").exists()


def test_lock_file_removed_on_release(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..ttyTEST0")
    with _lock.PortLock("/dev/ttyTEST0", sharing="exclusive"):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_exclusive_raises_when_port_busy(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..ttyTEST0")
    # PID 1 (init/systemd) always exists
    lock_path.write_text("         1\n")

    with pytest.raises(ok_serial.SerialOpenBusy):
        with _lock.PortLock("/dev/ttyTEST0", sharing="exclusive"):
            pass


def test_stomp_overwrites_existing_lock(fs, mocker):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..ttyTEST0")
    lock_path.write_text("         1\n")  # Owned by init

    # Mock os.kill to avoid actually signaling init
    mock_kill = mocker.patch("os.kill")

    with _lock.PortLock("/dev/ttyTEST0", sharing="stomp"):
        assert int(lock_path.read_text().strip()) == os.getpid()

    # Verify SIGTERM was sent to the owning process
    mock_kill.assert_any_call(1, signal.SIGTERM)


def test_missing_lock_directory_proceeds(fs):
    # Don't create /var/lock
    with _lock.PortLock("/dev/ttyTEST0", sharing="exclusive"):
        pass


#
# acquire_fd() — flock and TIOCEXCL behavior
#


def test_oblivious_skips_fd_lock(fs, mocker):
    fs.create_dir("/var/lock")
    mock_flock = mocker.patch("fcntl.flock")
    mock_ioctl = mocker.patch("fcntl.ioctl")

    with _lock.PortLock("/dev/test", sharing="oblivious") as lock:
        lock.attach_fd(fd=999)

    mock_flock.assert_not_called()
    mock_ioctl.assert_not_called()


def test_polite_probes_and_releases_flock(fs, mocker):
    import fcntl

    fs.create_dir("/var/lock")
    mock_flock = mocker.patch("fcntl.flock")
    mock_ioctl = mocker.patch("fcntl.ioctl")
    mocker.patch("termios.tcsetattr")

    with _lock.PortLock("/dev/test", sharing="polite") as lock:
        lock.attach_fd(fd=999)

    calls = [c[0] for c in mock_flock.call_args_list]
    assert (999, fcntl.LOCK_EX | fcntl.LOCK_NB) in calls
    assert (999, fcntl.LOCK_UN | fcntl.LOCK_NB) in calls
    # Polite never claims LOCK_SH (would shut out exclusive users).
    assert not any(arg & fcntl.LOCK_SH for _, arg in calls)
    # Polite never touches TIOCEXCL/TIOCNXCL.
    mock_ioctl.assert_not_called()


def test_polite_fails_when_flock_held(fs, mocker):
    fs.create_dir("/var/lock")
    mocker.patch("fcntl.flock", side_effect=BlockingIOError())
    mocker.patch("fcntl.ioctl")

    with _lock.PortLock("/dev/test", sharing="polite") as lock:
        with pytest.raises(ok_serial.SerialOpenBusy):
            lock.attach_fd(fd=999)


def test_exclusive_uses_flock_and_tiocexcl(fs, mocker):
    import fcntl
    import termios

    fs.create_dir("/var/lock")
    mock_flock = mocker.patch("fcntl.flock")
    mock_ioctl = mocker.patch("fcntl.ioctl")

    with _lock.PortLock("/dev/test", sharing="exclusive") as lock:
        lock.attach_fd(fd=999)

    # Exclusive claims LOCK_EX (relies on fd close to drop it; no LOCK_UN).
    flock_calls = [c[0] for c in mock_flock.call_args_list]
    assert (999, fcntl.LOCK_EX | fcntl.LOCK_NB) in flock_calls

    # Xclusive claims TIOCEXCL (relies on fd close to drop it; no TIOCNXCL).
    ioctl_calls = [c[0] for c in mock_ioctl.call_args_list]
    assert (999, termios.TIOCEXCL) in ioctl_calls


def test_exclusive_fails_when_flock_held(fs, mocker):
    fs.create_dir("/var/lock")
    mocker.patch("fcntl.flock", side_effect=BlockingIOError())
    mocker.patch("fcntl.ioctl")

    with _lock.PortLock("/dev/test", sharing="exclusive") as lock:
        with pytest.raises(ok_serial.SerialOpenBusy):
            lock.attach_fd(fd=999)


#
# check() — periodic intrusion detection
#


def test_check_returns_none_for_non_polite(fs):
    lock = _lock.PortLock("/dev/test", sharing="exclusive")
    assert lock.check() is None  # no fd yet
    lock.fd = 999  # pretend
    assert lock.check() is None  # exclusive: never returns intrusion


def test_check_detects_lock_file_appearing(fs, mocker):
    fs.create_dir("/var/lock")
    mocker.patch("fcntl.flock")

    with _lock.PortLock("/dev/ttyTEST0", sharing="polite") as lock:
        # Someone else creates the regular LCK..* file. Use PID 1 (init) so
        # `_lock_file_owner` sees a live owner that isn't us.
        Path("/var/lock/LCK..ttyTEST0").write_text("         1\n")
        with pytest.raises(ok_serial.SerialIoConflict):
            lock.check()
