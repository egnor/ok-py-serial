"""Unit tests for ok_serial._connection."""

import termios
import threading
import time
from ok_serial import _connection


def test_basic_connection(pty_serial):
    with _connection.SerialConnection(pty_serial.path, baud=57600) as _conn:
        tcattr = termios.tcgetattr(pty_serial.simulated.fileno())
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = tcattr
        assert ispeed == termios.B57600


def test_deadline_from_timeout(mocker):
    TMAX = threading.TIMEOUT_MAX
    mocker.patch("time.monotonic")
    time.monotonic.return_value = 1000.0

    assert _connection._deadline_from_timeout(-1) == 0
    assert _connection._deadline_from_timeout(0) == 0
    assert _connection._deadline_from_timeout(1) == 1001.0
    assert _connection._deadline_from_timeout(None) == TMAX
    assert _connection._deadline_from_timeout(TMAX - 1) == TMAX
    assert _connection._deadline_from_timeout(TMAX) == TMAX
    assert _connection._deadline_from_timeout(TMAX + 1) == TMAX


def test_timeout_from_deadline(mocker):
    TMAX = threading.TIMEOUT_MAX
    mocker.patch("time.monotonic")
    time.monotonic.return_value = 1000.0

    assert _connection._timeout_from_deadline(-1) == 0
    assert _connection._timeout_from_deadline(0) == 0
    assert _connection._timeout_from_deadline(999) == 0
    assert _connection._timeout_from_deadline(1000) == 0
    assert _connection._timeout_from_deadline(1001) == 1
    assert _connection._timeout_from_deadline(TMAX - 1) == TMAX - 1001
    assert _connection._timeout_from_deadline(TMAX) == TMAX
    assert _connection._timeout_from_deadline(TMAX + 1) == TMAX
