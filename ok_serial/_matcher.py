import dataclasses
import json
import logging
import re

from ok_serial._exceptions import SerialMatcherInvalid
from ok_serial._scanning import SerialPort

log = logging.getLogger("ok_serial.matching")


@dataclasses.dataclass(frozen=True)
class _Rules:
    pos: list[tuple[str, re.Pattern]]
    neg: list[tuple[str, re.Pattern]]
    use_latest: bool = False

    def __str__(self) -> str:
        return "\n".join(
            (["latest"] if self.use_latest else [])
            + [ap + "~/" + rx.pattern + "/" for ap, rx in self.pos]
            + [ap + "!~/" + rx.pattern + "/" for ap, rx in self.neg]
        )

    def pos_match(self, k: str, v: str) -> bool:
        return any(k.startswith(ap) and rx.search(v) for ap, rx in self.pos)

    def neg_match(self, k: str, v: str) -> bool:
        return any(k.startswith(ap) and rx.search(v) for ap, rx in self.neg)


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
_LATEST_RE = re.compile("^latest|newest$", re.I)

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
            rules = "".join(f"\n  {r}" for r in str(self._rules).splitlines())
            log.debug("Parsed %s:%s", repr(match), rules)

    def __repr__(self) -> str:
        return f"SerialPortMatcher({self._input!r})"

    def __str__(self) -> str:
        return self._input

    def __bool__(self) -> bool:
        return bool(
            self._rules.use_latest or self._rules.pos or self._rules.neg
        )

    def filter(self, ports: list[SerialPort]) -> list[SerialPort]:
        """Filters a list of ports according to match criteria."""

        matches = []
        for p in ports:
            if not any(self._rules.neg_match(k, v) for k, v in p.attr.items()):
                if any(self._rules.pos_match(k, v) for k, v in p.attr.items()):
                    matches.append(p)

        if self._rules.use_latest:
            matches = [m for m in matches if "time" in p.attr]
            matches.sort(reverse=True, key=lambda m: m.attr["time"])
            return matches[:1]

        return matches

    def hits(self, port: SerialPort) -> set[str]:
        """
        Returns the set of attribute keys matched by this expression,
        typically for display highlighting purposes.
        """

        attr = port.attr
        return set(k for k, v in attr.items() if self._rules.pos_match(k, v))


def _rules_from_str(match: str) -> _Rules:
    out = _Rules(pos=[], neg=[])
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
        out_list = out.neg if bool(rx_ex or eq_ex or bare_ex) else out.pos
        if rx_text:
            try:
                out_list.append((rx_att, re.compile(rx_text)))
            except re.error as ex:
                msg = f"Bad port matcher regex: /{rx_text}/"
                raise SerialMatcherInvalid(msg) from ex
        elif qstr:
            out_list.append((att, _str_rx(qstr, glob=True, full=bool(att))))
        elif num:
            value = int(f"0x{num[:-1]}" if num[-1:] in "hH" else num, 0)
            rx_text = f"({num}|0*{value}|(0x)?0*{value:x}h?)"
            pf, sf = ("^", "$") if att else (r"(?<![A-Z0-9])", r"(?![A-Z0-9])")
            out_list.append((att, re.compile(pf + rx_text + sf, re.I)))
        elif _LATEST_RE.match(naked) and not att and out_list is out.pos:
            out = dataclasses.replace(out, use_latest=True)
        elif naked:
            out_list.append((att, _str_rx(naked, glob=True, full=bool(att))))
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
