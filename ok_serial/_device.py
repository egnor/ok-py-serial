import msgspec
import re
import serial

import ok_serial._ports


_FIELD_MATCH_RE = re.compile(r'(?:\s*(?:([a-z_]+)\s*:)?\s*("[^"]*"|\S*))')


class SerialActivity(msgspec.Struct, frozen=True):
    connected_port: ok_serial._ports.PortIdentity | None
    received: bytes
    just_connected: bool = False
    just_disconnected: str | None = None


class SerialDevice:
    def __init__(self, spec: str, baud: int = 115200):
        self._baud = baud
        self._port_matcher = ok_serial._ports.PortMatcher(spec)
        self._port_id: ok_serial._ports.PortIdentity | None = None
        self._serial: serial.Serial | None = None
