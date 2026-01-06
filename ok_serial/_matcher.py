import json
import logging
import re

from ok_serial._exceptions import SerialMatcherInvalid
from ok_serial._scanning import SerialPort

log = logging.getLogger("ok_serial.matching")

_TERM_RE = re.compile(
    # beginning of term
    r"\s*(?<!\S)(?:"
    # vid:pid OR
    r"""([0-9A-F]{4}):([0-9A-F]{4})(?!\S)|"""
    # ( optional attr THEN ~/regex/ ) OR
    r"""(?:([A-Z_]*)~/((?:\\.|[^\\/])*)/)|"""
    # optional attr= THEN (
    r"""(?:([A-Z_]+)=)?(?:"""
    #   "str", 'str', OR
    r"""["']((?:\\.|[^\\"])*)["']|"""
    #   number OR
    r"""(0b[0-1]+|0o[0-7]+|[0-9]+|0x[0-9a-f]+|[0-9a-f]+h)|"""
    #   naked term
    r"""((?:\\.|[^\s\\"'=~])+)"""
    # ) end of term
    r"))(?!\S)\s*",
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

        if not match:
            log.debug("Parsed '' (any port)")
        elif log.isEnabledFor(logging.DEBUG):
            patterns = "".join(
                f"\n  {k}~/" + p.pattern.replace("/", "\\/") + "/"
                for k, p in self._patterns
            )
            log.debug("Parsed %s:%s", repr(match), patterns)

    def __repr__(self) -> str:
        return f"SerialPortMatcher({self._input!r})"

    def __str__(self) -> str:
        return self._input

    def __bool__(self) -> bool:
        return bool(self._patterns)

    def matches(self, port: SerialPort) -> bool:
        """True if this matcher selects 'port'"""

        return all(
            any(k.startswith(p) and r.search(v) for k, v in port.attr.items())
            for p, r in self._patterns
        )

    def matching_attrs(self, port: SerialPort) -> set[str]:
        """The set of attribute keys on 'port' matched by this matcher"""

        return set(
            k
            for k, v in port.attr.items()
            if any(k.startswith(p) and r.search(v) for p, r in self._patterns)
        )


def _patterns_from_str(match: str) -> list[tuple[str, re.Pattern]]:
    out: list[tuple[str, re.Pattern]] = []
    next_pos = 0
    while next_pos < len(match):
        tm = _TERM_RE.match(match, pos=next_pos)
        if not (tm and tm[0]):
            jmatch = json.dumps(match)  # use JSON for consistent quoting
            jpre = json.dumps(match[:next_pos])
            msg = f"Bad port matcher:\n  {jmatch}\n -{'-' * (len(jpre) - 1)}^"
            raise SerialMatcherInvalid(msg)

        next_pos = tm.end()
        vi, pi, ratt, rx, att, qv, num, naked = tm.groups(default="")
        if vi and pi:
            out.append(("vid", re.compile(f"^{int(vi, 16)}$")))
            out.append(("pid", re.compile(f"^{int(pi, 16)}$")))
        elif rx:
            try:
                out.append((ratt, re.compile(rx)))
            except re.error as ex:
                msg = f"Bad port matcher regex: /{rx}/"
                raise SerialMatcherInvalid(msg) from ex
        elif qv:
            out.append((att, _str_rx(qv, glob=False, full=bool(att))))
        elif num:
            value = int(f"0x{num[:-1]}" if num[-1:] in "hH" else num, 0)
            rx = f"({num}|0*{value}|(0x)?0*{value:x}h?)"
            pf, sf = ("^", "$") if att else (r"(?<![A-Z0-9])", r"(?![A-Z0-9])")
            out.append((att, re.compile(pf + rx + sf, re.I)))
        elif naked:
            out.append((att, _str_rx(naked, glob=True, full=bool(att))))
        else:
            assert False, f"bad term match: {tm[0]!r}"

    return out


def _str_rx(quoted: str, glob: bool, full: bool) -> re.Pattern:
    rx = ""
    next_pos = 0
    while next_pos < len(quoted):
        em = _ESCAPE_RE.match(quoted, pos=next_pos)
        assert em and em[0], "bad escape match: {quoted!r}"

        next_pos = em.end()
        uesc, star, qmark, literal = em.groups(default="")
        if uesc:
            try:
                rx += re.escape(uesc.encode().decode("unicode-escape"))
            except UnicodeDecodeError as ex:
                msg = f"Bad port matcher string {uesc}"
                raise SerialMatcherInvalid(msg) from ex
        elif star:
            rx += ".*" if glob else re.escape("*")
        elif qmark:
            rx += "." if glob else re.escape("?")
        elif literal:
            rx += re.escape(literal)

    prefix = "^" if full else r"(?<![A-Z0-9])" if rx[:1].isalnum() else ""
    suffix = "$" if full else r"(?![A-Z0-9])" if rx[-1:].isalnum() else ""
    return re.compile(prefix + rx + suffix, re.I)
