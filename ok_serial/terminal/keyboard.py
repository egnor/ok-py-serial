"""Decoding terminal input chunks into structured key events."""

import dataclasses
import re

# Unicode private-use codepoints used by the Kitty protocol for non-text keys.
# https://sw.kovidgoyal.net/kitty/keyboard-protocol/#functional-key-definitions
_KEY_CODES: dict[str, int] = {
    name: index
    for index, name in enumerate(
        "ESCAPE ENTER TAB BACKSPACE INSERT DELETE LEFT RIGHT UP DOWN "
        "PAGE_UP PAGE_DOWN HOME END CAPS_LOCK SCROLL_LOCK NUM_LOCK "
        "PRINT_SCREEN PAUSE MENU".split()
        + [f"F{n}" for n in range(1, 36)]
        + [f"KP_{n}" for n in range(10)]
        + "KP_DECIMAL KP_DIVIDE KP_MULTIPLY KP_SUBTRACT KP_ADD KP_ENTER "
        "KP_EQUAL KP_SEPARATOR KP_LEFT KP_RIGHT KP_UP KP_DOWN KP_PAGE_UP "
        "KP_PAGE_DOWN KP_HOME KP_END KP_INSERT KP_DELETE KP_BEGIN "
        "MEDIA_PLAY MEDIA_PAUSE MEDIA_PLAY_PAUSE MEDIA_REVERSE MEDIA_STOP "
        "MEDIA_FAST_FORWARD MEDIA_REWIND MEDIA_TRACK_NEXT "
        "MEDIA_TRACK_PREVIOUS MEDIA_RECORD LOWER_VOLUME RAISE_VOLUME "
        "MUTE_VOLUME LEFT_SHIFT LEFT_CONTROL LEFT_ALT LEFT_SUPER "
        "LEFT_HYPER LEFT_META RIGHT_SHIFT RIGHT_CONTROL RIGHT_ALT "
        "RIGHT_SUPER RIGHT_HYPER RIGHT_META "
        "ISO_LEVEL3_SHIFT ISO_LEVEL5_SHIFT".split(),
        start=57344,
    )
}

_KEY_NAMES = {code: name for name, code in _KEY_CODES.items()}

# single-byte keys
_BYTE_KEYS: dict[bytes, int] = {
    bytes([ch]): _KEY_CODES[name]
    for ch, name in zip((9, 13, 27, 127), "TAB ENTER ESCAPE BACKSPACE".split())
}

# classic keys sent as CSI [1;mods] letter; "R" (old F3) is omitted
# because CSI 1;mods R is ambiguous with a cursor position report
_CSI_LETTER_KEYS: dict[bytes, int] = {
    bytes([ch]): _KEY_CODES[name]
    for ch, name in zip(
        b"ABCDEFHPQSZ",
        "UP DOWN RIGHT LEFT KP_BEGIN END HOME F1 F2 F4 TAB".split(),
    )
}

# keys sent SS3-prefixed in application cursor/keypad modes (and F1-F4)
_SS3_KEYS: dict[bytes, int] = _CSI_LETTER_KEYS | {
    bytes([ch]): _KEY_CODES[name]
    for ch, name in zip(
        b"RMXjklmnopqrstuvwxy",
        "F3 KP_ENTER KP_EQUAL KP_MULTIPLY KP_ADD "
        "KP_SEPARATOR KP_SUBTRACT KP_DECIMAL KP_DIVIDE "
        "KP_0 KP_1 KP_2 KP_3 KP_4 KP_5 KP_6 KP_7 KP_8 KP_9".split(),
    )
}

# classic keys sent as CSI number [;mods] ~ (DEC VT220 style)
_TILDE_KEYS: dict[int, int] = {
    num: _KEY_CODES[name]
    for num, name in zip(
        (*range(35), 57427),
        "- HOME INSERT DELETE END PAGE_UP PAGE_DOWN HOME END - "  # 0-9
        "- F1 F2 F3 F4 F5 - F6 F7 F8 F9 F10 - F11 F12 F13 F14 "  # 10-25
        "- F15 F16 - F17 F18 F19 F20 KP_BEGIN".split(),
    )
    if name != "-"  # skip unused numbers
}

# key reports sent instead of plain bytes when the kitty keyboard protocol
# (CSI key:alts;mods:event;text u) or xterm modifyOtherKeys
# (CSI 27;mods;key ~) is active
_KITTY_KEY_RX = re.compile(
    b"(?:\x1b\\[|\x9b)(?:"
    b"27;(?P<xmods>[0-9]+);(?P<xkey>[0-9]+)~|"
    b"(?P<ukey>[0-9]+)(?::[0-9]*)*"
    b"(?:;(?P<umods>[0-9]+)(?::(?P<uevent>[0-9]+))?)?"
    b"(?:;(?P<utext>[0-9:]*))?u"
    b")"
)

# classic ANSI/DEC key sequences: CSI [1;mods] letter, CSI num [;mods] ~,
# and SS3 char; mods may carry a kitty ":event" subparameter
_CLASSIC_KEY_RX = re.compile(
    b"(?:\x1b\\[|\x9b)(?:"
    b"(?:1;(?P<lmods>[0-9]+(?::[0-9]+)?))?(?P<lkey>[A-Z])|"
    b"(?P<tkey>[0-9]+)(?:;(?P<tmods>[0-9]+(?::[0-9]+)?))?~"
    b")|"
    b"(?:\x1bO|\x8f)(?P<skey>[\x20-\x7e])"
)


@dataclasses.dataclass(frozen=True)
class TerminalKeyEvent:
    """A single key press (or autorepeat) decoded from terminal input."""

    key: int
    """Kitty-style codepoint for the base key (101 for ^E, 92 for ^\\, etc),
    using specific private-use codepoints for non-text keys (see _KEY_CODES)."""

    text: str = ""
    """Text inserted by this key, eg. "\\x1c" for ctrl-\\, "" if N/A."""

    shift: bool = False
    alt: bool = False
    ctrl: bool = False

    @property
    def name(self) -> str:
        return _KEY_NAMES.get(self.key, chr(self.key))


def chunk_to_key_event(chunk: bytes | str) -> TerminalKeyEvent | None:
    """Returns the TerminalKeyEvent for a terminal input chunk:
    - plain control bytes (0x00-0x1F, 0x7F)
    - key reports from the kitty keyboard protocol or xterm modifyOtherKeys
    - classic ANSI/DEC non-text key reports (arrows, F-keys, keypad...)
    Returns None for printable text input (str chunks), other escapes, etc."""

    if not isinstance(chunk, bytes):
        return None
    if key := _BYTE_KEYS.get(chunk):
        return TerminalKeyEvent(key, text=chunk.decode("ascii"))
    elif len(chunk) == 1 and (code := chunk[0]) < 0x20:
        key = ord(chr(code | 0x40).lower())
        return TerminalKeyEvent(key, text=chr(code), ctrl=True)
    if chunk == b"\x7f":
        return TerminalKeyEvent(0x7F, text="\x7f")  # backspace, kitty style

    if rxm := _KITTY_KEY_RX.fullmatch(chunk):
        if not (event := _kitty_key_event(rxm)):
            return None
    elif rxm := _CLASSIC_KEY_RX.fullmatch(chunk):
        if not (event := _classic_key_event(rxm)):
            return None
    else:
        return None

    if event.key < 0x20:  # some terminals report the resulting control code
        key = ord(chr(event.key | 0x40).lower())
        event = dataclasses.replace(event, key=key)
    if event.ctrl and not event.text and 0x40 <= event.key < 0x80:
        if 0x40 <= (upper := ord(chr(event.key).upper())) < 0x60:
            event = dataclasses.replace(event, text=chr(upper & 0x1F))
    return event


def _kitty_key_event(rxm: re.Match[bytes]) -> TerminalKeyEvent | None:
    if rxm["uevent"] not in (None, b"1", b"2"):
        return None  # ignore key release (3) and other non-press events
    mods = int(rxm["xmods"] or rxm["umods"] or b"1") - 1
    utext = rxm["utext"] or b""
    return TerminalKeyEvent(
        key=int(rxm["xkey"] or rxm["ukey"]),
        text="".join(chr(int(cp)) for cp in utext.split(b":") if cp),
        shift=bool(mods & 1),
        alt=bool(mods & 2),
        ctrl=bool(mods & 4),
    )


def _classic_key_event(rxm: re.Match[bytes]) -> TerminalKeyEvent | None:
    if rxm["lkey"] is not None:
        key = _CSI_LETTER_KEYS.get(rxm["lkey"])
        mods_field = rxm["lmods"]
    elif rxm["skey"] is not None:
        key = _SS3_KEYS.get(rxm["skey"])
        mods_field = None
    else:
        key = _TILDE_KEYS.get(int(rxm["tkey"]))
        mods_field = rxm["tmods"]
    if key is None:
        return None

    mods_str, _, event_type = (mods_field or b"1").partition(b":")
    if event_type not in (b"", b"1", b"2"):
        return None  # ignore key release (3) and other non-press events

    mods = int(mods_str) - 1 | (1 if rxm["lkey"] == b"Z" else 0)
    return TerminalKeyEvent(
        key, shift=bool(mods & 1), alt=bool(mods & 2), ctrl=bool(mods & 4)
    )
