import re

# regexp to match style-relevant terminal escape sequences
# https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
STYLE_CODE_RX = re.compile(
    # group 1: Set Graphics Rendition (SGR) content
    b"(?:\x1b\\[|\x9b)(.*m)|"
    # group 2: DECSC / DECRC (DEC Save/Restore Cursor) command
    b"\x1b([78])|"
    # group 3: XTPUSHSGR / XTPOPSGR (XTerm Push/Pop SGR) command
    b"(?:\x1b\\[|\x9b)#([{}pq])"
)

# regexp to match individual codes within SGR content, each ending in m or ;.
# codes in the same category supercede (latest wins)
SGR_CODE_RX = re.compile(
    b"(?:"
    b"(?P<reset>0?)|"  # 0 or empty parameter: reset all attributes
    b"(?P<weight>1|2|22)|"  # bold / faint / normal intensity
    b"(?P<slant>3|20|23)|"  # italic / Fraktur / neither (23 cancels both)
    b"(?P<under>4(?::\\d+)?|21|24)|"  # single (or 4:n styled) / double / off
    b"(?P<blink>5|6|25)|"  # slow / rapid / off
    b"(?P<inverse>7|27)|"  # reverse video / off
    b"(?P<hidden>8|28)|"  # conceal / reveal
    b"(?P<strike>9|29)|"  # crossed-out / off
    b"(?P<font>1[0-9])|"  # primary (10) or alternative (11-19) font
    b"(?P<prop>26|50)|"  # proportional spacing on / off
    b"(?P<frame>5[12]|54)|"  # framed / encircled / off
    b"(?P<over>53|55)|"  # overlined / off
    b"(?P<ideogram>6[0-5])|"  # CJK side/under/over lines (60-64) / off (65)
    b"(?P<fg>3[0-79]|9[0-7]|38;5;\\d+|38;2;\\d+;\\d+;\\d+|38:[\\d:]*)|"
    b"(?P<bg>4[0-79]|10[0-7]|48;5;\\d+|48;2;\\d+;\\d+;\\d+|48:[\\d:]*)|"
    b"(?P<ul>58;5;\\d+|58;2;\\d+;\\d+;\\d+|58:[\\d:]*|59)|"  # underline color
    b"(?P<script>7[345])|"  # super- / sub-script / off
    b"(?P<other>[^m;]+)"
    b")[m;]"
)


class TerminalStyleSaver:
    """Buffers VTxxx text-style settings for interpretation and restoration."""

    def __init__(self) -> None:
        self.codes: dict[bytes, bytes] = {}
        self._dec_save: dict[bytes, bytes] = {}
        self._xt_stack: list[dict[bytes, bytes]] = []

    def add_escape(self, chunk: bytes) -> None:
        """If a terminal escape (from TerminalChunker) is text-style-relevant,
        incorporates it into the saved style. Handles SGR, DEC(SC/RC) and
        XT(PUSH/POP)SGR; does not capture doublewidth modes, pixel graphics,
        custom characters, custom palettes, or other semi esoteric features.
        Ignores cursor movement, scrolling and other non-style-setting codes.
        """

        if match := STYLE_CODE_RX.fullmatch(chunk):
            sgr, dec_save, xt_push = match.groups()
            if sgr is not None:
                pos = 0
                while pos < len(sgr):
                    sgr_match = SGR_CODE_RX.match(sgr, pos)
                    assert sgr_match, sgr[pos:]
                    pos, name = sgr_match.end(), sgr_match.lastgroup
                    assert name, sgr[pos:]  # some named group always matches
                    if name == "reset":
                        self.codes.clear()
                    else:
                        # category codes key by name (latest in category wins);
                        # "other" codes key by value so distinct ones accumulate
                        value = sgr_match.group(name)
                        key = value if name == "other" else name.encode()
                        self.codes.pop(key, None)  # reorder to latest
                        self.codes[key] = value
            elif dec_save == b"7":
                self._dec_save = {**self.codes}
            elif dec_save == b"8":
                self.codes = {**self._dec_save}
            elif xt_push in (b"{", b"p"):
                self._xt_stack.append({**self.codes})
            elif xt_push in (b"}", b"q"):
                if self._xt_stack:
                    self.codes = self._xt_stack.pop()

    def get_escape(self) -> bytes:
        """Returns an SGR escape code to restore the accumulated text style."""

        return b"\x1b[;" + b";".join(self.codes.values()) + b"m"
