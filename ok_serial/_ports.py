import fnmatch
import msgspec
import natsort
import re
import serial.tools.list_ports


class PortIdentity(msgspec.Struct, frozen=True, order=True):
    """What we know about a potentially available serial port on the system"""

    id: str
    attr: dict[str, str]


class PortMatcher:
    """A parsed expression for filtering desired PortIdentity objects"""

    _TERM_RE = re.compile(
        r'(\s*)(?:(\w+)\s*:\s*)?("(?:\\.|[^"\\])*"|(?:\\.|[^:"\s\\])*)'
    )

    def __init__(self, spec: str):
        """Parses string 'spec' as a fielded glob matcher on port attributes"""

        current_field = ""
        globs: dict[str, str] = {}
        pos = 0
        while pos < len(spec):
            match = PortMatcher._TERM_RE.match(spec, pos=pos)
            if not (match and match.group(0)):
                esc_spec = spec.encode("unicode-escape").decode()
                esc_pos = len(spec[:pos].encode("unicode-escape").decode())
                raise ValueError(
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

        self._patterns = {
            k: re.compile(fnmatch.translate(g), re.I) for k, g in globs.items()
        }

    def matches(self, port: PortIdentity) -> bool:
        """Tests this matcher against port attributes"""

        for k, rx in self._patterns.items():
            if k == "*" and any(rx.match(v) for v in port.attr.values()):
                continue
            if not rx.match(port.attr.get(k, "")):
                return False
        return True


def scan_ports() -> list[PortIdentity]:
    """Returns a list of PortIdentity for ports found on the current system"""

    def convert(p: serial.tools.list_ports_common.ListPortInfo) -> PortIdentity:
        _NA = (None, "", "n/a")
        attr = {k.lower(): str(v) for k, v in vars(p).items() if v not in _NA}
        return PortIdentity(p.device, attr)

    return list(
        natsort.natsorted(
            (convert(p) for p in serial.tools.list_ports.comports()),
            key=lambda p: p.id,
            alg=natsort.ns.PATH,
        )
    )
