import fnmatch
import logging
import natsort
import os
import pathlib
import re
from serial.tools import list_ports
from serial.tools import list_ports_common

import pydantic

from ok_serial import _exceptions

log = logging.getLogger("ok_serial.scanning")


class SerialPortAttributes(pydantic.BaseModel):
    """What we know about a potentially available serial port on the system"""

    model_config = pydantic.ConfigDict(frozen=True)
    port: str
    attr: dict[str, str]


class SerialPortMatcher:
    """A parsed expression for matching against SerialPortAttributes results"""

    _POSINT_RE = re.compile(r"0|[1-9][0-9]*|0x[0-9a-f]+", re.I)

    _TERM_RE = re.compile(
        r'(\s*)(?:(\w+)\s*:\s*)?("(?:\\.|[^"\\])*"|(?:\\.|[^:"\s\\])*)'
    )

    @pydantic.validate_call
    def __init__(self, spec: str):
        """Parses string 'spec' as a fielded glob matcher on port attributes"""

        current_field = ""
        globs: dict[str, str] = {}
        pos = 0
        while pos < len(spec):
            match = SerialPortMatcher._TERM_RE.match(spec, pos=pos)
            if not (match and match.group(0)):
                esc_spec = spec.encode("unicode-escape").decode()
                esc_pos = len(spec[:pos].encode("unicode-escape").decode())
                raise _exceptions.SerialMatcherInvalid(
                    f"Bad port spec:\n  [{esc_spec}]\n  -{'-' * esc_pos}^"
                )

            pos = match.end()
            wspace, field, value = match.groups(default="")
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1].encode().decode("unicode-escape", "ignore")
            if field:
                current_field = field.rstrip().rstrip(":").strip().lower()
                globs[current_field] = value
            elif current_field:
                globs[current_field] += wspace + value
            else:
                current_field = "*"
                globs[current_field] = wspace + value

        self._patterns = {}
        for k, glob in globs.items():
            if SerialPortMatcher._POSINT_RE.fullmatch(glob):
                num = int(glob, 0)
                regex = f"({glob}|{num}|(0x)?0*{num:x}h?)\\Z"
            else:
                regex = fnmatch.translate(glob)
            self._patterns[k] = re.compile(regex, re.I)

        self.spec = spec
        log.debug("Parsed %s (%s)", repr(spec), ", ".join(globs.keys()))

    def __repr__(self) -> str:
        return f"SerialPortMatcher({self.spec!r})"

    @pydantic.validate_call
    def matches(self, port: SerialPortAttributes) -> bool:
        """Tests this matcher against port attributes"""

        for k, rx in self._patterns.items():
            if k == "*" and any(rx.match(v) for v in port.attr.values()):
                continue
            if not rx.match(port.attr.get(k, "")):
                return False
        return True


_scan_override_adapter = pydantic.TypeAdapter(dict[str, dict[str, str]])


@pydantic.validate_call
def scan_serial_ports() -> list[SerialPortAttributes]:
    """Returns a list of serial ports found on the current system"""

    ov_path = os.getenv("OK_SERIAL_SCAN_OVERRIDE")
    if ov_path:
        try:
            ov_data = pathlib.Path(ov_path).read_bytes()
            ov = _scan_override_adapter.validate_json(ov_data)
        except (OSError, ValueError) as ex:
            msg = f"Can't read $OK_SERIAL_SCAN_OVERRIDE {ov_path}"
            raise _exceptions.SerialScanException(msg) from ex

        out = [SerialPortAttributes(port=p, attr=a) for p, a in ov.items()]
        log.debug("$OK_SERIAL_SCAN_OVERRIDE (%s): %d ports", ov_path, len(out))
        return out

    def conv(p: list_ports_common.ListPortInfo) -> SerialPortAttributes:
        _NA = (None, "", "n/a")
        attr = {k.lower(): str(v) for k, v in vars(p).items() if v not in _NA}
        return SerialPortAttributes(port=p.device, attr=attr)

    try:
        out = [conv(p) for p in list_ports.comports()]
    except OSError as ex:
        raise _exceptions.SerialScanException("Can't scan serial ports") from ex

    out.sort(key=natsort.natsort_keygen(key=lambda p: p.port, alg=natsort.ns.P))
    log.debug("Scanned %d serial ports", len(out))
    return out
