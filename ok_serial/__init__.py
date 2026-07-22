"""
A Python serial port library (based on [PySerial](https://www.pyserial.com/))
with improved port discovery and I/O semantics.
[(Usage guide)](https://github.com/egnor/ok-py-serial#readme)
"""

from ok_serial._connection import (
    SerialConnection,
    SerialConnectionOptions,
    SerialControlSignals,
)

from ok_serial._scan import scan_serial_ports
from ok_serial._metadata import SerialPort
from ok_serial._monitor import SerialConnectionMonitor, SerialMonitorOptions
from ok_serial._lock import SerialSharingType

from ok_serial._exceptions import (
    SerialException,
    SerialIoClosed,
    SerialIoConflict,
    SerialIoException,
    SerialIoUnsupported,
    SerialMonitorExhausted,
    SerialOpenBusy,
    SerialOpenException,
    SerialScanException,
)

import importlib.metadata

__all__ = [n for n in globals() if not n.startswith("_")]

__version__ = importlib.metadata.version(__package__)

for _name in __all__:
    globals()[_name].__module__ = "ok_serial"
