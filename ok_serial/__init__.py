"""
Serial port library (PySerial wrapper) with improved discovery,
port sharing semantics, and interface.
"""

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

from ok_serial._scanning import (
    SerialPort,
    SerialPortMatcher,
    scan_serial_ports,
)

from ok_serial._tracker import SerialTracker, TrackerOptions

from beartype.claw import beartype_this_package as _beartype_me

_beartype_me()

__all__ = [n for n in dir() if not n.startswith("_")]
