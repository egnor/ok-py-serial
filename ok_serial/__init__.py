"""
Serial port library (PySerial wrapper) with improved discovery,
port sharing semantics, and interface.
"""

from beartype.claw import beartype_this_package as _beartype_me

# ruff: noqa: E402
_beartype_me()

from ok_serial._connection import (
    SerialConnection,
    SerialOptions,
    SerialSignals,
)

from ok_serial._exceptions import (
    OkSerialException,
    SerialIoClosed,
    SerialIoException,
    SerialMatcherInvalid,
    SerialOpenBusy,
    SerialOpenException,
    SerialScanException,
)

from ok_serial._locking import SerialSharingType
from ok_serial._matcher import SerialPortMatcher
from ok_serial._scanning import SerialPort, scan_serial_ports
from ok_serial._tracker import SerialPortTracker, TrackerOptions

__all__ = [n for n in dir() if not n.startswith("_")]
