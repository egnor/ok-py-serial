import logging
import re

from ok_serial import _exceptions
from ok_serial import _scanning

log = logging.getLogger("ok_serial.matching")

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

_ESCAPE_RE = re.compile(
    # unicode-escape OR
    r"(\\[a-zA-Z0-9\\n][^\\*?]*)|"
    # glob wildcards OR
    r"([*])|([?])|"
    # literal
    r"\\?(.[^\\*?]*)"
)


class SerialPortMatcher:
    """A parsed expression for matching against SerialPort results"""

    def __init__(self, match: str):
        """Parses string 'match' as fielded globs matching port attributes"""

        self._input = match
        self._patterns = _patterns_from_str(match)

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

    def matches(self, port: _scanning.SerialPort) -> bool:
        """True if this matcher selects 'port'"""

        return all(
            any(self._amatch(pk, prx, ak, av) for ak, av in port.attr.items())
            for pk, prx in self._patterns
        )

    def matching_attrs(self, port: _scanning.SerialPort) -> set[str]:
        """The set of attribute keys on 'port' matched by this matcher"""

        return set(
            ak
            for ak, av in port.attr.items()
            if any(self._amatch(pk, prx, ak, av) for pk, prx in self._patterns)
        )

    def _amatch(self, pk: str, prx: re.Pattern, ak: str, av: str) -> bool:
        return (pk == "*" or ak.startswith(pk)) and bool(prx.search(av))


def _patterns_from_str(match: str) -> list[tuple[str, re.Pattern]]:
    out: list[tuple[str, re.Pattern]] = []
    next_pos = 0
    while next_pos < len(match):
        tm = _TERM_RE.match(match, pos=next_pos)
        if not (tm and tm[0]):
            repr_pos = len(repr(match[:next_pos])) - 1
            msg = f"Bad port matcher:\n  {match!r}\n -{'-' * repr_pos}^"
            raise _exceptions.SerialMatcherInvalid(msg)

        next_pos = tm.end()
        vi, pi, ratt, rx, qatt, qop, qv, num, naked = tm.groups(default="")
        if vi and pi:
            out.append(("vid", re.compile(f"^{int(vi, 16)}$")))
            out.append(("pid", re.compile(f"^{int(pi, 16)}$")))
        elif rx:
            try:
                out.append((ratt or "*", re.compile(rx)))
            except re.error as ex:
                msg = f"Bad port matcher regex: /{rx}/"
                raise _exceptions.SerialMatcherInvalid(msg) from ex
        elif qv:
            rx = _rx_from_str(qv, glob=False)
            rx = f"^{rx}$" if qop == "=" else rx
            out.append((qatt or "*", re.compile(rx)))
        elif num:
            nv = int(num, 0)
            rx = r"(?<!\w)" f"(0*{nv}|(0x)?0*{nv:x}h?)" r"(?!\w)"
            out.append(("*", re.compile(rx)))
        elif naked:
            rx = _rx_from_str(naked, glob=True)
            out.append(("*", re.compile(rx, re.I)))
        else:
            assert False, f"bad term match: {tm[0]!r}"

    return out


def _rx_from_str(quoted: str, glob: bool) -> str:
    out = ""
    next_pos = 0
    while next_pos < len(quoted):
        em = _ESCAPE_RE.match(quoted, pos=next_pos)
        assert em and em[0], "bad escape match: {quoted!r}"

        next_pos = em.end()
        uesc, star, qmark, literal = em.groups(default="")
        if uesc:
            try:
                out += re.escape(uesc.encode().decode("unicode-escape"))
            except UnicodeDecodeError as ex:
                msg = f"Bad port matcher string {uesc}"
                raise _exceptions.SerialMatcherInvalid(msg) from ex
        elif star:
            out += ".*" if glob else re.escape("*")
        elif qmark:
            out += "." if glob else re.escape("*")
        elif literal:
            out += re.escape(literal)

    out = r"(?<!\w)" + out if out[:1].isalnum() else out
    out = out + r"(?!\w)" if out[-1:].isalnum() else out
    return out
