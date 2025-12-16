"""Unit tests for ok_serial._locking."""

import os
from pathlib import Path

import pytest
import signal

from ok_serial import _exceptions
from ok_serial import _locking


#
# Lock file path generation
#


def test_lock_path_for_regular_device(fs):
    fs.create_dir("/var/lock")
    with _locking.using_lock_file("/dev/ttyUSB0", sharing="exclusive"):
        assert Path("/var/lock/LCK..ttyUSB0").exists()


def test_lock_path_for_pty_device(fs):
    fs.create_dir("/var/lock")
    with _locking.using_lock_file("/dev/pts/5", sharing="exclusive"):
        assert Path("/var/lock/LCK..pts.5").exists()


#
# Lock file ownership
#


def test_lock_file_owner_returns_pid(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..test")
    lock_path.write_text(f"{os.getpid():>10d}\n")

    assert _locking._lock_file_owner(lock_path) == os.getpid()


def test_lock_file_owner_returns_none_for_missing_file(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..test")

    assert _locking._lock_file_owner(lock_path) is None


def test_lock_file_owner_removes_stale_lock(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..test")
    lock_path.write_text("999999999\n")  # Non-existent PID

    assert _locking._lock_file_owner(lock_path) is None
    assert not lock_path.exists()


def test_lock_file_owner_removes_invalid_content(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..test")
    lock_path.write_text("not a number\n")

    assert _locking._lock_file_owner(lock_path) is None
    assert not lock_path.exists()


#
# Sharing modes for file locks
#


def test_oblivious_skips_lock_file(fs):
    fs.create_dir("/var/lock")
    with _locking.using_lock_file("/dev/ttyUSB0", sharing="oblivious"):
        assert not Path("/var/lock/LCK..ttyUSB0").exists()


def test_polite_creates_lock_file(fs):
    fs.create_dir("/var/lock")
    with _locking.using_lock_file("/dev/ttyUSB0", sharing="polite"):
        lock_path = Path("/var/lock/LCK..ttyUSB0")
        assert lock_path.exists()
        assert int(lock_path.read_text().strip()) == os.getpid()


def test_exclusive_creates_lock_file(fs):
    fs.create_dir("/var/lock")
    with _locking.using_lock_file("/dev/ttyUSB0", sharing="exclusive"):
        lock_path = Path("/var/lock/LCK..ttyUSB0")
        assert lock_path.exists()
        assert int(lock_path.read_text().strip()) == os.getpid()


def test_lock_file_removed_on_exit(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..ttyUSB0")

    with _locking.using_lock_file("/dev/ttyUSB0", sharing="exclusive"):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_raises_when_port_busy(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..ttyUSB0")
    # PID 1 (init/systemd) always exists
    lock_path.write_text("         1\n")

    with pytest.raises(_exceptions.SerialOpenBusy):
        with _locking.using_lock_file("/dev/ttyUSB0", sharing="exclusive"):
            pass


def test_stomp_overwrites_existing_lock(fs, mocker):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..ttyUSB0")
    lock_path.write_text("         1\n")  # Owned by init

    # Mock os.kill to avoid actually signaling init
    mock_kill = mocker.patch("os.kill")

    with _locking.using_lock_file("/dev/ttyUSB0", sharing="stomp"):
        assert int(lock_path.read_text().strip()) == os.getpid()

    # Verify SIGTERM was sent to the owning process
    mock_kill.assert_any_call(1, signal.SIGTERM)


def test_reentry_allowed_same_process(fs):
    fs.create_dir("/var/lock")
    lock_path = Path("/var/lock/LCK..ttyUSB0")

    with _locking.using_lock_file("/dev/ttyUSB0", sharing="exclusive"):
        # Nested entry should succeed since we own the lock
        with _locking.using_lock_file("/dev/ttyUSB0", sharing="exclusive"):
            assert lock_path.exists()


def test_missing_lock_directory_proceeds(fs):
    # Don't create /var/lock
    with _locking.using_lock_file("/dev/ttyUSB0", sharing="exclusive"):
        pass  # Should succeed without error


#
# FD locking (mocked since pyfakefs doesn't support flock)
#


def test_fd_lock_oblivious_skips_locking(mocker):
    mock_flock = mocker.patch("fcntl.flock")
    mock_ioctl = mocker.patch("fcntl.ioctl")

    with _locking.using_fd_lock("/dev/test", fd=999, sharing="oblivious"):
        pass

    mock_flock.assert_not_called()
    mock_ioctl.assert_called()  # TIOCNXCL on exit


def test_fd_lock_polite_uses_shared_lock(mocker):
    import fcntl

    mock_flock = mocker.patch("fcntl.flock")
    mocker.patch("fcntl.ioctl")

    with _locking.using_fd_lock("/dev/test", fd=999, sharing="polite"):
        pass

    # Should call: LOCK_EX, LOCK_UN, LOCK_SH (acquire pattern), then LOCK_UN (release)
    assert mock_flock.call_count >= 3
    calls = [c[0] for c in mock_flock.call_args_list]
    assert (999, fcntl.LOCK_EX | fcntl.LOCK_NB) in calls
    assert (999, fcntl.LOCK_SH | fcntl.LOCK_NB) in calls


def test_fd_lock_exclusive_uses_exclusive_lock(mocker):
    import fcntl

    mock_flock = mocker.patch("fcntl.flock")
    mocker.patch("fcntl.ioctl")

    with _locking.using_fd_lock("/dev/test", fd=999, sharing="exclusive"):
        pass

    calls = [c[0] for c in mock_flock.call_args_list]
    assert (999, fcntl.LOCK_EX | fcntl.LOCK_NB) in calls


def test_fd_lock_raises_when_busy(mocker):
    mocker.patch("fcntl.flock", side_effect=BlockingIOError())
    mocker.patch("fcntl.ioctl")

    with pytest.raises(_exceptions.SerialOpenBusy):
        with _locking.using_fd_lock("/dev/test", fd=999, sharing="exclusive"):
            pass


def test_fd_lock_uses_tiocexcl(mocker):
    import termios

    mocker.patch("fcntl.flock")
    mock_ioctl = mocker.patch("fcntl.ioctl")

    with _locking.using_fd_lock("/dev/test", fd=999, sharing="exclusive"):
        pass

    calls = [c[0] for c in mock_ioctl.call_args_list]
    assert (999, termios.TIOCEXCL) in calls
    assert (999, termios.TIOCNXCL) in calls
