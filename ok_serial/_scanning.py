import dataclasses
import json
import logging
import natsort
import os
import pathlib
from serial.tools import list_ports
from serial.tools import list_ports_common

from ok_serial._exceptions import SerialScanException

log = logging.getLogger("ok_serial.scanning")


@dataclasses.dataclass(frozen=True)
class SerialPort:
    """Metadata about a serial port found on the system"""

    """The OS device identifier, eg. `/dev/ttyUSB3`, 'COM4', etc."""
    name: str

    """
    Descriptive port attributes, see
    [serial port attributes](https://github.com/egnor/py-ok-serial#serial-port-attributes) for a list.
    """
    attr: dict[str, str]

    def __str__(self):
        return self.name


def scan_serial_ports() -> list[SerialPort]:
    """
    Returns a list of serial ports currently attached to the system.

    For testing and encapsulation, if the environment variable
    `$OK_SERIAL_SCAN_OVERRIDE` is the pathname of a JSON file in
    `{"port-name": {"attr": "value", ...}, ...}` format, that port listing
    is returned instead of actual system scan results.

    Raises:
    - `SerialScanException`: System error scanning ports
    """

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
            raise SerialScanException(msg) from ex

        out = [SerialPort(name=p, attr=a) for p, a in ov_data.items()]
        log.debug("$OK_SERIAL_SCAN_OVERRIDE (%s): %d ports", ov, len(out))
    else:
        try:
            ports = list_ports.comports()
        except OSError as ex:
            raise SerialScanException("Can't scan serial") from ex

        out = [_convert_port(p) for p in ports]

    out.sort(key=natsort.natsort_keygen(key=lambda p: p.name, alg=natsort.ns.P))
    log.debug("Found %d ports", len(out))
    return out


def _convert_port(p: list_ports_common.ListPortInfo) -> SerialPort:
    _NA = (None, "", "n/a")
    attr = {k.lower(): str(v) for k, v in vars(p).items() if v not in _NA}
    if p.vid and p.pid:
        attr["vid_pid"] = f"{p.vid:04x}:{p.pid:04x}"
    return SerialPort(name=p.device, attr=attr)
