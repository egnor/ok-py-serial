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

    _TERM_RE = re.compile(
        # beginning of term
        r"\s*(?<!\S)(?:"
        # vid:pid OR
        r"""([0-9A-F]{4}):([0-9A-F]{4})(?!\S)|"""
        # attr~/regex/, ~/regex/ OR
        r"""([A-Z_]+)?~/((?:\\.|[^\\/])*)/|"""
        # attr:"str", attr:'str', "str", 'str' OR
        r"""(?:([A-Z_]+)(:|=))?["']((?:\\.|[^\\"])*)["']|"""
        # naked number OR
        r"""(0|[1-9][0-9]*|0x[0-9a-f]+)|"""
        # naked term
        r"""((?:\\.|[^\\\s"'~])+)"""
        # end of term
        r")(?!\S)\s*",
        re.I,
    )

    _FNMATCH_RE = re.compile(r"^\(\?s:(.*)\)\\Z")

    def __init__(self, match: str):
        """Parses string 'match' as fielded globs matching port attributes"""

        self._input = match
        self._patterns: list[tuple[str, re.Pattern]] = []

        pos = 0
        while pos < len(match):
            tm = self._TERM_RE.match(match, pos=pos)
            if not (tm and tm[0]):
                repr_pos = len(repr(match[:pos])) - 1
                msg = f"Bad port matcher:\n  {match!r}\n -{'-' * repr_pos}^"
                raise _exceptions.SerialMatcherInvalid(msg)

            vi, pi, ratt, rx, qatt, qop, qv, num, naked = tm.groups(default="")
            pos = tm.end()

            if vi and pi:
                self._patterns.append(("vid", re.compile(f"^{int(vi, 16)}$")))
                self._patterns.append(("pid", re.compile(f"^{int(pi, 16)}$")))

            elif rx:
                try:
                    self._patterns.append((ratt or "*", re.compile(rx)))
                except re.error as ex:
                    msg = f"Bad port matcher regex: /{rx}/"
                    raise _exceptions.SerialMatcherInvalid(msg) from ex

            elif qv:
                try:
                    unquoted = qv.encode().decode("unicode-escape")
                except UnicodeDecodeError as ex:
                    msg = f"Bad port matcher string {qv}"
                    raise _exceptions.SerialMatcherInvalid(msg) from ex
                rx = re.escape(unquoted)
                rx = f"^{rx}$" if qop == "=" else rx
                rx = r"(?<!\w)" + rx if unquoted[:1].isalnum() else rx
                rx = rx + r"(?!\w)" if unquoted[-1:].isalnum() else rx
                self._patterns.append((qatt or "*", re.compile(rx)))

            elif num:
                nv = int(num, 0)
                rx = r"(?<!\w)" f"(0*{nv}|(0x)?0*{nv:x}h?)" r"(?!\w)"
                self._patterns.append(("*", re.compile(rx)))

            elif naked:
                rx = fnmatch.translate(naked)
                rx = m[1] if (m := self._FNMATCH_RE.match(rx)) else rx
                rx = r"(?<!\w)" + rx if naked[:1].isalnum() else rx
                rx = rx + r"(?!\w)" if naked[-1:].isalnum() else rx
                self._patterns.append(("*", re.compile(rx, re.I)))

            else:
                assert False, f"bad regexp match: {tm[0]!r}"

        if log.isEnabledFor(logging.DEBUG):
            patterns = "".join(
                f"\n  {k.replace('*', '')}~/{pat}/"
                for k, p in self._patterns
                for pat in [p.pattern.replace("/", r"\/")]
            )
            log.debug("Parsed %s:%s", repr(match), patterns)

    def __repr__(self) -> str:
        return f"SerialPortMatcher({self._input!r})"

    def __str__(self) -> str:
        return self._input

    def matches(self, port: SerialPort) -> bool:
        """True if this matcher selects 'port'"""

        return all(
            any(self._amatch(pk, prx, ak, av) for ak, av in port.attr.items())
            for pk, prx in self._patterns
        )

    def matching_attrs(self, port: SerialPort) -> set[str]:
        """The set of attribute keys on 'port' matched by this matcher"""

        return set(
            ak
            for ak, av in port.attr.items()
            if any(self._amatch(pk, prx, ak, av) for pk, prx in self._patterns)
        )

    def _amatch(self, pk: str, prx: re.Pattern, ak: str, av: str) -> bool:
        return (pk == "*" or ak.startswith(pk)) and bool(prx.search(av))


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
