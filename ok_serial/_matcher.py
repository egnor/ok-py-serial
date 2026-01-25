import dataclasses
import json
import logging
import re

from ok_serial._exceptions import SerialMatcherInvalid
from ok_serial._scanning import SerialPort

log = logging.getLogger("ok_serial.matching")


@dataclasses.dataclass(frozen=True)
class _MatchRule:
    prefix: str
    rx: re.Pattern
    inv: bool

    def __str__(self) -> str:
        quoted = self.rx.pattern.replace("/", r"\/")
        return f"{self.prefix}{'!' if self.inv else ''}~/{quoted}/"

    def matches(self, k: str, v: str) -> bool:
        relevant = not self.inv and k.startswith(self.prefix)
        return relevant and bool(self.rx.search(v))

    def forbids(self, k: str, v: str) -> bool:
        relevant = self.inv and k.startswith(self.prefix)
        return relevant and bool(self.rx.search(v))


_TERM_RE = re.compile(
    # beginning of term
    r"\s*(?<!\S)(?:"
    # ( optional <attr> THEN optional <!> THEN ~/<regex>/ ) OR
    r"""(?:([A-Z_]*)(!?)~/((?:\\.|[^\\/])*)/)|"""
    # ( optional ( <attr> then optional <!> then = ) OR optional <!> ) THEN (
    r"""(?:([A-Z_]+)(!?)=|(!?))(?:"""
    #   quote <str> quote OR
    r"""["']((?:\\.|[^\\"])*)["']|"""
    #   <number> OR
    r"""(0b[0-1]+|0o[0-7]+|[0-9]+|0x[0-9a-f]+|[0-9a-f]+h)|"""
    #   <naked-term>
    r"""((?:\\.|[^\s\\!"'=~])+)"""
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
    """A parsed expression for identifying serial ports of interest."""

    def __init__(self, match: str):
        """Parses a
        [serial port match expression](https://github.com/egnor/ok-py-serial#serial-port-match-expressions)
        in preparation to identify matching `SerialPort` objects.
        """

        self._input = match
        self._rules = _rules_from_str(match)

        if not match:
            log.debug("Parsed '' (any port)")
        elif log.isEnabledFor(logging.DEBUG):
            rules = "".join(f"\n  {r}" for r in self._rules)
            log.debug("Parsed %s:%s", repr(match), rules)

    def __repr__(self) -> str:
        return f"SerialPortMatcher({self._input!r})"

    def __str__(self) -> str:
        return self._input

    def __bool__(self) -> bool:
        return bool(self._rules)

    def filter(self, ports: list[SerialPort]) -> list[SerialPort]:
        """Filters a list of ports according to match criteria."""

        return [
            p
            for p in ports
            if all(
                any(rule.matches(k, v) for k, v in p.attr.items())
                and not any(rule.forbids(k, v) for k, v in p.attr.items())
                for rule in self._rules
            )
        ]

    def hits(self, port: SerialPort) -> set[str]:
        """
        Returns the set of attribute keys matched by this expression,
        typically for display highlighting purposes.
        """

        return set(
            k
            for k, v in port.attr.items()
            if any(rule.matches(k, v) for rule in self._rules)
        )


def _rules_from_str(match: str) -> list[_MatchRule]:
    out: list[_MatchRule] = []
    next_pos = 0
    while next_pos < len(match):
        tm = _TERM_RE.match(match, pos=next_pos)
        if not (tm and tm[0]):
            jmatch = json.dumps(match)  # use JSON for consistent quoting
            jpre = json.dumps(match[:next_pos])
            msg = f"Bad port matcher:\n  {jmatch}\n -{'-' * (len(jpre) - 1)}^"
            raise SerialMatcherInvalid(msg)

        next_pos, groups = tm.end(), tm.groups(default="")
        rx_att, rx_ex, rx_text, att, eq_ex, bare_ex, qstr, num, naked = groups
        inv = bool(rx_ex or eq_ex or bare_ex)
        if rx_text:
            try:
                rx = re.compile(rx_text)
                out.append(_MatchRule(prefix=rx_att, rx=rx, inv=inv))
            except re.error as ex:
                msg = f"Bad port matcher regex: /{rx_text}/"
                raise SerialMatcherInvalid(msg) from ex
        elif qstr:
            rx = _str_rx(qstr, glob=True, full=bool(att))
            out.append(_MatchRule(prefix=att, rx=rx, inv=inv))
        elif num:
            value = int(f"0x{num[:-1]}" if num[-1:] in "hH" else num, 0)
            rx_text = f"({num}|0*{value}|(0x)?0*{value:x}h?)"
            pf, sf = ("^", "$") if att else (r"(?<![A-Z0-9])", r"(?![A-Z0-9])")
            rx = re.compile(pf + rx_text + sf, re.I)
            out.append(_MatchRule(prefix=att, rx=rx, inv=inv))
        elif naked:
            rx = _str_rx(naked, glob=True, full=bool(att))
            out.append(_MatchRule(prefix=att, rx=rx, inv=inv))
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
