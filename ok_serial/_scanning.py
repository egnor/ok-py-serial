import dataclasses
import fnmatch
import json
import logging
import natsort
import os
import pathlib
import re
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


class SerialPortMatcher:
    """A parsed expression for matching against SerialPort results"""

    _POSINT_RE = re.compile(r"0|[1-9][0-9]*|0x[0-9a-f]+", re.I)

    _TERM_RE = re.compile(
        r"""(\s*)(?:(\w+)\s*:\s*)?"""  # whitespace, field
        r"""(["'](?:\\.|[^"\\])*["']|(?:\\.|[^:"'\s\\])*)"""  # value
    )

    _VIDPID_RE = re.compile(r"([0-9a-f]{4}):([0-9a-f]{4})", re.I)

    def __init__(self, match: str):
        """Parses string 'match' as fielded globs matching port attributes"""

        self._input = match
        self._patterns: dict[str, re.Pattern] = {}

        current_field = ""
        globs: dict[str, str] = {}
        pos = 0
        while pos < len(match):
            term = SerialPortMatcher._TERM_RE.match(match, pos=pos)
            if not (term and term.group(0)):
                match_esc = match.encode("unicode-escape").decode()
                esc_pos = len(match[:pos].encode("unicode-escape").decode())
                msg = f"Bad port matcher:\n  [{match_esc}]\n  -{'-' * esc_pos}^"
                raise _exceptions.SerialMatcherInvalid(msg)

            pos = term.end()
            if vidpid := SerialPortMatcher._VIDPID_RE.fullmatch(term.group(0)):
                globs["vid"] = f"0x{vidpid[1]}"
                globs["pid"] = f"0x{vidpid[2]}"
                continue

            wspace, field, value = term.groups(default="")
            if (value[:1] + value[-1:]) in ('""', "''"):
                try:
                    value = value[1:-1].encode().decode("unicode-escape")
                except UnicodeDecodeError as ex:
                    msg = f"Bad port matcher value: {value}"
                    raise _exceptions.SerialMatcherInvalid(msg) from ex
            if field:
                current_field = field.rstrip().rstrip(":").strip().lower()
                globs[current_field] = value
            elif current_field:
                globs[current_field] += wspace + value
            else:
                current_field = "*"
                globs[current_field] = wspace + value

        for k, glob in globs.items():
            if SerialPortMatcher._POSINT_RE.fullmatch(glob):
                num = int(glob, 0)
                regex = f"({glob}|{num}|(0x)?0*{num:x}h?)\\Z"
            else:
                regex = fnmatch.translate(glob)
            self._patterns[k] = re.compile(regex, re.I)

        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "Parsed %s:%s",
                repr(match),
                "".join(
                    f"\n  {k}: /{p.pattern}/" for k, p in self._patterns.items()
                ),
            )

    def __repr__(self) -> str:
        return f"SerialPortMatcher({self._input!r})"

    def __str__(self) -> str:
        return self._input

    def matches(self, port: SerialPort) -> bool:
        """Tests this matcher against port attributes"""

        for k, rx in self._patterns.items():
            if not any(
                (k == "*" or ak.startswith(k)) and rx.match(av)
                for ak, av in port.attr.items()
            ):
                return False
        return True


def scan_serial_ports(
    match: str | SerialPortMatcher | None = None,
) -> list[SerialPort]:
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

        found = [SerialPort(name=p, attr=a) for p, a in ov_data.items()]
        log.debug("$OK_SERIAL_SCAN_OVERRIDE (%s): %d ports", ov, len(found))
    else:
        try:
            ports = list_ports.comports()
        except OSError as ex:
            raise _exceptions.SerialScanException("Can't scan serial") from ex

        found = [_convert_port(p) for p in ports]

    if match:
        if isinstance(match, str):
            match = SerialPortMatcher(match)
        out = [p for p in found if match.matches(p)]
        nf, no = len(found), len(out)
        log.debug("Found %d ports, %d match %r", nf, no, str(match))
    else:
        out = found
        log.debug("Found %d ports", len(out))

    out.sort(key=natsort.natsort_keygen(key=lambda p: p.name, alg=natsort.ns.P))
    return out


def _convert_port(p: list_ports_common.ListPortInfo) -> SerialPort:
    _NA = (None, "", "n/a")
    attr = {k.lower(): str(v) for k, v in vars(p).items() if v not in _NA}
    return SerialPort(name=p.device, attr=attr)
