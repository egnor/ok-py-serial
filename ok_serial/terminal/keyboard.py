"""Decoding terminal input chunks into structured key events."""

import dataclasses
import re

# key reports sent instead of plain bytes when the kitty keyboard protocol
# (CSI key:alts;mods:event;text u) or xterm modifyOtherKeys
# (CSI 27;mods;key ~) is active
_KEY_REPORT_RX = re.compile(
    b"(?:\x1b\\[|\x9b)(?:"
    b"27;(?P<xmods>[0-9]+);(?P<xkey>[0-9]+)~|"
    b"(?P<ukey>[0-9]+)(?::[0-9]*)*"
    b"(?:;(?P<umods>[0-9]+)(?::(?P<uevent>[0-9]+))?)?"
    b"(?:;(?P<utext>[0-9:]*))?u"
    b")"
)


@dataclasses.dataclass(frozen=True)
class TerminalKeyEvent:
    """A single key press (or autorepeat) decoded from terminal input."""

    key: int
    """Unicode codepoint of the key without modifiers applied, kitty style
    (lowercase for letters), e.g. 92 (backslash) for ctrl-\\."""

    text: str = ""
    """Text this key press inserts, when known: as reported by the
    terminal, or the legacy character for a control combo, e.g. "\\x1c"
    for ctrl-\\ however it was encoded."""

    shift: bool = False
    alt: bool = False
    ctrl: bool = False


def chunk_to_key_event(chunk: bytes | str) -> TerminalKeyEvent | None:
    """Decodes a terminal input chunk as a key event, if it unambiguously
    represents a single key press: a plain control byte, or a CSI key
    report from the kitty keyboard protocol or xterm modifyOtherKeys.
    Ordinary printable text (str chunks), other escape sequences, and
    key-release reports return None."""

    if not isinstance(chunk, bytes):
        return None
    if len(chunk) == 1 and (code := chunk[0]) < 0x20:
        key = ord(chr(code | 0x40).lower())
        return TerminalKeyEvent(key, text=chr(code), ctrl=True)
    if not (rxm := _KEY_REPORT_RX.fullmatch(chunk)):
        return None
    if rxm["uevent"] not in (None, b"1", b"2"):
        return None  # ignore key release (3) and other non-press events

    mods = int(rxm["xmods"] or rxm["umods"] or b"1") - 1
    utext = rxm["utext"] or b""
    event = TerminalKeyEvent(
        key=int(rxm["xkey"] or rxm["ukey"]),
        text="".join(chr(int(cp)) for cp in utext.split(b":") if cp),
        shift=bool(mods & 1),
        alt=bool(mods & 2),
        ctrl=bool(mods & 4),
    )

    if event.key < 0x20:  # some terminals report the resulting control code
        key = ord(chr(event.key | 0x40).lower())
        event = dataclasses.replace(event, key=key)
    if event.ctrl and not event.text and 0x40 <= event.key < 0x80:
        if 0x40 <= (upper := ord(chr(event.key).upper())) < 0x60:
            event = dataclasses.replace(event, text=chr(upper & 0x1F))
    return event
