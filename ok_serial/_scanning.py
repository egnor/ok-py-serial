import dataclasses
import json
import logging
import natsort
import os
import pathlib
from serial.tools import list_ports
from serial.tools import list_ports_common

from ok_serial import _exceptions

log = logging.getLogger("ok_serial.scanning")


@dataclasses.dataclass(frozen=True)
class SerialPort:
    """What we know about a potentially available serial port on the system"""

    name: str
    attr: dict[str, str]

    def __str__(self):
        return self.name


def scan_serial_ports() -> list[SerialPort]:
    """Returns a list of serial ports found on the current system"""

    if ov := os.getenv("OK_SERIAL_SCAN_OVERRIDE"):
        try:
            ov_data = json.loads(pathlib.Path(ov).read_text())
            if not isinstance(ov_data, dict) or not all(
                isinstance(attr, dict)
                and all(isinstance(aval, str) for aval in attr.values())
                for attr in ov_data.values()
            ):
                raise ValueError("Override data is not a dict of dicts")
        except (OSError, ValueError) as ex:
            msg = f"Can't read $OK_SERIAL_SCAN_OVERRIDE {ov}"
            raise _exceptions.SerialScanException(msg) from ex

        out = [SerialPort(name=p, attr=a) for p, a in ov_data.items()]
        log.debug("$OK_SERIAL_SCAN_OVERRIDE (%s): %d ports", ov, len(out))
    else:
        try:
            ports = list_ports.comports()
        except OSError as ex:
            raise _exceptions.SerialScanException("Can't scan serial") from ex

        out = [_convert_port(p) for p in ports]

    out.sort(key=natsort.natsort_keygen(key=lambda p: p.name, alg=natsort.ns.P))
    log.debug("Found %d ports", len(out))
    return out


def _convert_port(p: list_ports_common.ListPortInfo) -> SerialPort:
    _NA = (None, "", "n/a")
    attr = {k.lower(): str(v) for k, v in vars(p).items() if v not in _NA}
    return SerialPort(name=p.device, attr=attr)
