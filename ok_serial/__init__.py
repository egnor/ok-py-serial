"""
A Python serial port library
(based on [PySerial](https://www.pyserial.com/))
with improved port discovery
and I/O semantics.
[(Usage guide)](https://github.com/egnor/ok-py-serial#readme)
"""

from beartype.claw import beartype_this_package as _beartype_me

# ruff: noqa: E402
_beartype_me()

from ok_serial._connection import (
    SerialConnection,
    SerialConnectionOptions,
    SerialControlSignals,
)

from ok_serial._scanning import scan_serial_ports, SerialPort
from ok_serial._matcher import SerialPortMatcher
from ok_serial._tracker import SerialPortTracker, TrackerOptions
from ok_serial._locking import SerialSharingType

from ok_serial._exceptions import (
    SerialException,
    SerialIoClosed,
    SerialIoException,
    SerialMatcherInvalid,
    SerialOpenBusy,
    SerialOpenException,
    SerialScanException,
)

__all__ = [n for n in globals() if not n.startswith("_")]
