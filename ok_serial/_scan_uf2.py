import json
import logging
import natsort
import os
import os.path
import psutil
import re

from ok_serial._exceptions import SerialScanException
from ok_serial._matching import compile_match
from ok_serial._port import PortInfo, PortPredicate

log = logging.getLogger("ok_serial.scan_uf2")

_PATH_SORT_KEY = natsort.natsort_keygen(alg=natsort.ns.P)
_INFO_SORT_KEY = natsort.natsort_keygen(key=lambda p: p.name, alg=natsort.ns.P)
_UF2_HEADER_RX = re.compile(r"^([^\s]+):(.*)$")


def scan_uf2_devices(
    match: str | PortPredicate | None = None,
) -> list[PortInfo]:
    """
    Returns a list of mounted [UF2](https://microsoft.github.io/uf2/)
    filesystems. (These are not serial ports, but may be of common interest.)

    If set, `match` is a
    [match string](https://github.com/egnor/ok-py-serial#port-matching)
    or `PortInfo -> bool` callable to filter the devices returned.

    For testing and encapsulation, if the environment variable
    `$OK_SERIAL_SCAN_UF2_OVERRIDE` is the pathname of a JSON file in
    `{"path-name": {"attr": "value", ...}, ...}` format, that port listing
    is returned instead of actual system scan results.

    Raises:
    - `SerialScanException` - System error scanning filesystems.
    """

    if ov_path := os.getenv("OK_SERIAL_SCAN_UF2_OVERRIDE"):
        # Externally overridden device list
        try:
            with open(ov_path) as file:
                found = _devices_from_json_text(file.read())
        except (OSError, ValueError) as ex:
            msg = f"Can't read $OK_SERIAL_SCAN_UF2_OVERRIDE {ov_path}"
            raise SerialScanException(msg) from ex

        log.debug("Read $OK_SERIAL_SCAN_OVERRIDE %s", ov_path)
    else:
        try:
            partitions = psutil.disk_partitions()
        except OSError as ex:
            raise SerialScanException("Can't scan mount points") from ex

        # Include `match` in the list of dirs in case it's a direct pathname
        mpoints = set(os.path.realpath(p.mountpoint) for p in partitions)
        if match_path := isinstance(match, str) and os.path.realpath(match):
            mpoints.add(match_path)

        found = []
        for mpoint in mpoints:
            if dev := _device_from_dir(mpoint):
                found.append(dev)
                if mpoint == match_path:
                    assert isinstance(match, str)
                    dev.attr["path_found"] = match

    if match:
        culled = list(filter(compile_match(match), found))
        n_found, n_match = len(found), len(culled)
        log.debug("Found %d UF2 devices, %d match %r", n_found, n_match, match)
    else:
        culled = found
        log.debug("Found %d UF2 devices", len(found))

    culled.sort(key=_INFO_SORT_KEY)
    return culled


def _device_from_dir(dir: str) -> PortInfo | None:
    info_path = os.path.join(dir, "INFO_UF2.TXT")
    try:
        with open(info_path) as file:
            info_lines = file.readlines()
    except FileNotFoundError:
        return None
    except OSError as ex:
        log.error("Error reading: %s", info_path, exc_info=ex)
        return None

    attr = {"device": dir}
    for line in info_lines:
        if not attr.get("uf2"):
            attr["uf2"] = line.strip()
        elif m := _UF2_HEADER_RX.match(line.strip()):
            key, val = m.groups()
            attr[key.strip().lower()] = val.strip()
        else:
            log.warning("Bad %s line: %r", info_path, line.strip())

    if not attr.get("uf2") or not attr.get("board-id"):
        content = "".join(f"\n  {line}" for line in info_lines)
        log.error("Bad %s content:%s", info_path, content)
        return None

    return PortInfo(name=dir, attr=attr)


def _devices_from_json_text(text: str) -> list[PortInfo]:
    jv = json.loads(text)
    if not isinstance(jv, dict) or not all(
        isinstance(pv, dict) and all(isinstance(v, str) for v in pv.values())
        for pv in jv.values()
    ):
        raise ValueError(f"Bad type: {jv!r}")

    return [PortInfo(name=k, attr=v) for k, v in jv.items()]
