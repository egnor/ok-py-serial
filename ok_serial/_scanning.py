import dataclasses
import datetime
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

    name: str
    """The OS device identifier, eg. `/dev/ttyUSB3`, 'COM4', etc."""

    attr: dict[str, str]
    """
    [Metadata](https://github.com/egnor/py-ok-serial#serial-port-attributes)
    """

    def __str__(self):
        return self.name

    def key(self) -> str:
        return f"{self.name}@{self.attr.get('time', '')}"


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

    if ov_path := os.getenv("OK_SERIAL_SCAN_OVERRIDE"):
        try:
            out = _ports_from_json_text(pathlib.Path(ov_path).read_text())
        except (OSError, ValueError) as ex:
            msg = f"Can't read $OK_SERIAL_SCAN_OVERRIDE {ov_path}"
            raise SerialScanException(msg) from ex

        log.debug("Read $OK_SERIAL_SCAN_OVERRIDE %s", ov_path)
    else:
        try:
            ports = list_ports.comports()
        except OSError as ex:
            raise SerialScanException("Can't scan serial") from ex
        out = [_port_from_pyserial(p) for p in ports]

    out.sort(key=natsort.natsort_keygen(key=lambda p: p.name, alg=natsort.ns.P))
    log.debug("Found %d ports", len(out))
    return out


def _port_from_pyserial(p: list_ports_common.ListPortInfo) -> SerialPort:
    _NA = (None, "", "n/a")
    attr = {k.lower(): str(v) for k, v in vars(p).items() if v not in _NA}

    if p.vid and p.pid:
        attr["vid_pid"] = f"{p.vid:04x}:{p.pid:04x}"

    try:
        st = os.stat(p.device)
    except OSError:
        pass
    else:
        dt = datetime.datetime.fromtimestamp(st.st_mtime_ns * 1e-9)
        attr["time"] = dt.isoformat()

    return SerialPort(name=p.device, attr=attr)


def _ports_from_json_text(text: str) -> list[SerialPort]:
    jv = json.loads(text)
    if not isinstance(jv, dict) or not all(
        isinstance(pv, dict) and all(isinstance(v, str) for v in pv.values())
        for pv in jv.values()
    ):
        raise ValueError(f"Bad type: {jv!r}")

    return [SerialPort(name=k, attr=v) for k, v in jv.items()]
