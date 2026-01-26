import dataclasses
import json
import logging
import re

from ok_serial._exceptions import SerialMatcherInvalid
from ok_serial._scanning import SerialPort

log = logging.getLogger("ok_serial.matching")


@dataclasses.dataclass(frozen=True)
class _Rule:
    prefix: str
    rx: re.Pattern

    def __str__(self) -> str:
        quoted = self.rx.pattern.replace("/", r"\/")
        return f"{self.prefix}~/{quoted}/"

    def match(self, k: str, v: str) -> bool:
        return bool(k.startswith(self.prefix) and self.rx.search(v))


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

# naked term to indicate port uptime preference
_OLDEST_RE = re.compile("^earliest|oldest$", re.I)
_NEWEST_RE = re.compile("^latest|newest$", re.I)

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
        self._pos: list[_Rule] = []
        self._neg: list[_Rule] = []
        self._oldest = False
        self._newest = False

        next_pos = 0
        while next_pos < len(match):
            tm = _TERM_RE.match(match, pos=next_pos)
            if not (tm and tm[0]):
                jmatch = json.dumps(match)  # use JSON for consistent quoting
                jpre = json.dumps(match[:next_pos])
                msg = f"Bad port expr:\n  {jmatch}\n -{'-' * (len(jpre) - 1)}^"
                raise SerialMatcherInvalid(msg)

            next_pos, groups = tm.end(), tm.groups(default="")
            rx_att, rx_ex, rx_text, att, eq_ex, ex, qstr, num, naked = groups
            out_list = self._neg if bool(rx_ex or eq_ex or ex) else self._pos
            if rx_text:
                try:
                    out_list.append(_Rule(rx_att, re.compile(rx_text)))
                except re.error as ex:
                    msg = f"Bad port regex: /{rx_text}/"
                    raise SerialMatcherInvalid(msg) from ex
            elif qstr:
                rx = _qstr_rx(qstr, glob=False, full=bool(att))
                out_list.append(_Rule(att, rx))
            elif num:
                if num[-1:] in "hH":
                    value = int("{num[:-1]", 16)
                elif num.isdigit():
                    value = int(num, 10)  # int('0123', 0) is an error!
                else:
                    value = int(num, 0)
                rx_text = f"({num}|0*{value}|(0x)?0*{value:x}h?)"
                rx = _wrap_rx(rx_text, full=bool(att), wb=True, we=True)
                out_list.append(_Rule(att, rx))
            elif _OLDEST_RE.match(naked) and not att and out_list is self._pos:
                self._oldest = True
            elif _NEWEST_RE.match(naked) and not att and out_list is self._pos:
                self._newest = True
            elif naked:
                rx = _qstr_rx(naked, glob=True, full=bool(att))
                out_list.append(_Rule(att, rx))
            else:
                assert False, f"bad term match: {tm[0]!r}"

        if self._oldest and self._newest:
            msg = f"oldest & newest: {match!r}"
            raise SerialMatcherInvalid(msg)
        elif not self:
            log.debug("Parsed %s (any port)", repr(match))
        elif log.isEnabledFor(logging.DEBUG):
            rules = "".join(f"\n  {r}" for r in self.patterns())
            log.debug("Parsed %s:%s", repr(match), rules)

    def __repr__(self) -> str:
        return f"SerialPortMatcher({self._input!r})"

    def __str__(self) -> str:
        return self._input

    def __bool__(self) -> bool:
        return bool(self._oldest or self._newest or self._pos or self._neg)

    def patterns(self) -> list[str]:
        return [
            *(["oldest"] if self._oldest else []),
            *(["newest"] if self._newest else []),
            *[str(r).replace("~/", "!~/", 1) for r in self._neg],
            *[str(r) for r in self._pos],
        ]

    def filter(self, ports: list[SerialPort]) -> list[SerialPort]:
        """Filters a list of ports according to match criteria."""

        matches = []
        for p in ports:
            attrs = list(p.attr.items())
            pos = all(any(r.match(k, v) for k, v in attrs) for r in self._pos)
            neg = any(r.match(k, v) for k, v in attrs for r in self._neg)
            if pos and not neg:
                matches.append(p)

        if self._oldest or self._newest:
            timed = [m for m in matches if "time" in p.attr]
            timed.sort(key=lambda m: m.attr["time"])
            return timed[:1] if self._oldest else timed[-1:]

        return matches

    def hits(self, port: SerialPort) -> set[str]:
        """
        Returns the set of attribute keys matched by this expression,
        typically for display highlighting purposes.
        """

        return set(
            k
            for k, v in port.attr.items()
            if any(r.match(k, v) for r in self._pos)
        )


def _qstr_rx(quoted="", *, glob=False, full=False) -> re.Pattern:
    rx, next_pos = "", 0
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

    return _wrap_rx(rx, full=full, wb=rx[:1].isalnum(), we=rx[-1:].isalnum())


def _wrap_rx(rx: str, *, full=False, wb=False, we=False):
    prefix = "^" if full else r"(?<![A-Z0-9])" if wb else ""
    suffix = "$" if full else r"(?![A-Z0-9])" if we else ""
    return re.compile(prefix + rx + suffix, re.I)
