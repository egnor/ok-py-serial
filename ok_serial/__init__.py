"""
Serial port library (PySerial wrapper) with improved discovery,
port sharing semantics, and interface.
"""

from ok_serial._connection import SerialConnection
from ok_serial._locking import SerialSharingType
from ok_serial._scanning import (
    SerialPortAttributes,
    SerialPortMatcher,
    scan_serial_ports,
)
