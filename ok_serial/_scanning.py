import datetime
import json
import logging
import natsort
import os
import pathlib
import struct
from serial.tools import list_ports
from serial.tools import list_ports_common

from ok_serial._exceptions import SerialScanException
from ok_serial._matching import compile_match
from ok_serial._metadata import SerialPort, PortPredicate

log = logging.getLogger("ok_serial.scanning")

_HASHMASK = (1 << (struct.calcsize("L") * 8)) - 1
_HASHCODE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def scan_serial_ports(
    match: str | PortPredicate | None = None,
) -> list[SerialPort]:
    """
    Returns a list of serial ports currently attached to the system.

    If set, `match` is a
    [match string](https://github.com/egnor/ok-py-serial#port-matching)
    or `SerialPort -> bool` callable to filter the ports returned.

    For testing and encapsulation, if the environment variable
    `$OK_SERIAL_SCAN_OVERRIDE` is the pathname of a JSON file in
    `{"port-name": {"attr": "value", ...}, ...}` format, that port listing
    is returned instead of actual system scan results.

    Raises:
    - `SerialScanException` - System error scanning ports
    """

    if ov_path := os.getenv("OK_SERIAL_SCAN_OVERRIDE"):
        try:
            found = _ports_from_json_text(pathlib.Path(ov_path).read_text())
        except (OSError, ValueError) as ex:
            msg = f"Can't read $OK_SERIAL_SCAN_OVERRIDE {ov_path}"
            raise SerialScanException(msg) from ex

        log.debug("Read $OK_SERIAL_SCAN_OVERRIDE %s", ov_path)
    else:
        try:
            pyserial_ports = list_ports.comports()
        except OSError as ex:
            raise SerialScanException("Can't scan serial") from ex
        found = []
        for pyserial_port in pyserial_ports:
            if port := _port_from_pyserial(pyserial_port):
                found.append(port)

    sort_key = natsort.natsort_keygen(key=lambda p: p.name, alg=natsort.ns.P)
    found.sort(key=sort_key)

    if match is not None:
        cull = list(filter(compile_match(match), found))
        log.debug("Found %d ports, %d match %r", len(found), len(cull), match)
        return cull
    else:
        log.debug("Found %d ports", len(found))
        return found


def _port_from_pyserial(
    p: list_ports_common.ListPortInfo,
) -> SerialPort | None:
    # filter out bogus serial8250 entries on Linux (ttyS0~ttyS31)
    # https://stackoverflow.com/questions/2530096/how-to-find-all-serial-devices-ttys-ttyusb-on-linux-without-opening-them/12301542#12301542
    # https://askubuntu.com/questions/1520139/pyserial-lists-incorrect-serialports-on-ubuntu-24-04
    # https://forum.lazarus.freepascal.org/index.php/topic,69437.0.html
    dev_path = getattr(p, "device_path", "")
    if "serial8250" in dev_path:
        fd = -1
        try:
            fd = os.open(p.device, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
            if not os.isatty(fd):
                return None
        except OSError:
            return None
        finally:
            if fd >= 0:
                os.close(fd)

    _NA = (None, "", "n/a")
    attr = {k.lower(): str(v) for k, v in vars(p).items() if v not in _NA}

    # set "tid" to tio-compatible topology ID (base62 of djb2 hash)
    # https://github.com/tio/tio/blob/6fb3a64ba234cc255f9637ba938cf0c01e132e4a/src/tty.c#L1754
    # TODO: make this compatible on Mac also?
    if hash_path := getattr(p, "usb_interface_path", dev_path):
        hash, tid = 5381, ""
        for ch in hash_path:
            hash = ((hash << 5) + hash + ord(ch)) & _HASHMASK
        for b in range(4):
            tid += _HASHCODE[hash % len(_HASHCODE)]
            hash //= len(_HASHCODE)
        attr["tid"] = tid

    # set "time" to creation time of port (tracker prefers newer ports)
    try:
        st = os.stat(p.device)
    except OSError:
        pass
    else:
        dt = datetime.datetime.fromtimestamp(st.st_mtime_ns * 1e-9)
        attr["time"] = dt.isoformat(timespec="milliseconds")

    # set "vid_pid" to standard format XXXX:XXXX
    if p.vid and p.pid:
        attr["vid_pid"] = f"{p.vid:04x}:{p.pid:04x}"

    return SerialPort(name=p.device, attr=attr)


def _ports_from_json_text(text: str) -> list[SerialPort]:
    jv = json.loads(text)
    if not isinstance(jv, dict) or not all(
        isinstance(pv, dict) and all(isinstance(v, str) for v in pv.values())
        for pv in jv.values()
    ):
        raise ValueError(f"Bad type: {jv!r}")

    return [SerialPort(name=k, attr=v) for k, v in jv.items()]
