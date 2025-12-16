"""Unit tests for ok_serial._timeout_math."""

import time

from ok_serial import _timeout_math


def test_timeout_to_deadline(mocker):
    TMAX = _timeout_math.TIMEOUT_MAX
    mocker.patch("time.monotonic")
    time.monotonic.return_value = 1000.0

    assert _timeout_math.to_deadline(-1) == 0
    assert _timeout_math.to_deadline(0) == 0
    assert _timeout_math.to_deadline(1) == 1001.0
    assert _timeout_math.to_deadline(None) == TMAX
    assert _timeout_math.to_deadline(TMAX - 1) == TMAX
    assert _timeout_math.to_deadline(TMAX) == TMAX
    assert _timeout_math.to_deadline(TMAX + 1) == TMAX


def test_timeout_from_deadline(mocker):
    TMAX = _timeout_math.TIMEOUT_MAX
    mocker.patch("time.monotonic")
    time.monotonic.return_value = 1000.0

    assert _timeout_math.from_deadline(-1) == 0
    assert _timeout_math.from_deadline(0) == 0
    assert _timeout_math.from_deadline(999) == 0
    assert _timeout_math.from_deadline(1000) == 0
    assert _timeout_math.from_deadline(1001) == 1
    assert _timeout_math.from_deadline(TMAX - 1) == TMAX - 1001
    assert _timeout_math.from_deadline(TMAX) == TMAX
    assert _timeout_math.from_deadline(TMAX + 1) == TMAX
