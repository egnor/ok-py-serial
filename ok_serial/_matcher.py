import dataclasses
import json
import logging
import re

from ok_serial._exceptions import SerialMatcherInvalid
from ok_serial._scanning import SerialPort

log = logging.getLogger("ok_serial.matching")


class _MatchRule(dataclasses.dataclass):
    prefix: str
    rx: re.Pattern
    neg: bool

    def __str__(self) -> str:
        pat = f"/{self.rx.pattern.replace('/', '[/]')}/"
        return f"{self.prefix}*{'!' if self.invert else ''}~{pat}"

    def matches(self, k: str, v: str) -> bool:
        return not self.neg and k.startswith(self.prefix) and self.rx.search(v)

    def forbids(self, k: str, v: str) -> bool:
        return self.neg and k.startswith(self.prefix) and self.rx.search(v)


_TERM_RE = re.compile(
    # beginning of term
    r"\s*(?<!\S)(?:"
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

    def matches(self, port: SerialPort) -> bool:
        """Returns True if the given 'port' matches the parsed expression."""

        return all(
            any(rule.matches(k, v) for k, v in port.attr.items())
            and not any(rule.forbids(k, v) for k, v in port.attr.items())
            for rule in self._rules
        )

    def matching_attrs(self, port: SerialPort) -> set[str]:
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

        next_pos = tm.end()
        ratt, rx, att, qv, num, naked = tm.groups(default="")
        if rx:
            try:
                out.append(_MatchRule(prefix=ratt, rx=re.compile(rx)))
            except re.error as ex:
                msg = f"Bad port matcher regex: /{rx}/"
                raise SerialMatcherInvalid(msg) from ex
        elif qv:
            out.append(
                _MatchRule(
                    prefix=att, rx=_str_rx(qv, glob=False, full=bool(att))
                )
            )
        elif num:
            value = int(f"0x{num[:-1]}" if num[-1:] in "hH" else num, 0)
            ex = f"({num}|0*{value}|(0x)?0*{value:x}h?)"
            pf, sf = ("^", "$") if att else (r"(?<![A-Z0-9])", r"(?![A-Z0-9])")
            rx = re.compile(pf + ex + sf, re.I)
            out.append(_MatchRule(prefix=att, rx=rx))
        elif naked:
            out.append(
                _MatchRule(
                    prefix=att, rx=_str_rx(naked, glob=True, full=bool(att))
                )
            )
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
