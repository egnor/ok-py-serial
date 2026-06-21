import re

# regexp to match relevant terminal escape sequences
# https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
ESCAPE_CODE_RX = re.compile(
    # group 1: Set Graphics Rendition (SGR) content
    b"(?:\x1b\\[|\x9b)(.*m)|"
    # group 2: DECSC / DECRC (DEC Save/Restore Cursor) command
    b"\x1b([78])|"
    # group 3: XTPUSHSGR / XTPOPSGR (XTerm Push/Pop SGR) command
    b"(?:\x1b\\[|\x9b)#([{}pq])"
)

# regexp to match individual SGR content codes, each ending in m or ;
# codes in the same category supercede (latest wins)
SGR_SUBCODE_RX = re.compile(
    b"(?:"
    b"(?P<RESET>0?)|"  # 0 or empty parameter: reset all attributes
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
    b"(?P<OTHER>[^m;]+)"
    b")[m;]"
)


class TerminalStyleSaver:
    """Buffers VTxxx text-style settings for interpretation and restoration.

    Currently captures:
    - SGR codes (text style, color, font, etc.), including save/restore

    Does not capture:
    - Cursor position, scrolling, or other non-style-setting codes
    - Doublewidth, pixel graphics, custom palettes, or other esoteric styles
    """

    def __init__(self) -> None:
        self.sgr_codes: dict[bytes, bytes] = {}
        self._sgr_save_dec: dict[bytes, bytes] = {}
        self._sgr_save_xterm: list[dict[bytes, bytes]] = []

    def add_escape(self, chunk: bytes) -> None:
        """Incorporates an escape (from TerminalChunker) into saved state."""

        if match := ESCAPE_CODE_RX.fullmatch(chunk):
            sgr, dec_save, xt_push = match.groups()
            if sgr is not None:
                pos = 0
                while pos < len(sgr):
                    sgr_match = SGR_SUBCODE_RX.match(sgr, pos)
                    assert sgr_match, sgr[pos:]
                    pos, name = sgr_match.end(), sgr_match.lastgroup
                    assert name, sgr[pos:]  # some named group always matches
                    if name == "RESET":
                        self.sgr_codes.clear()
                    else:
                        # category codes key by name (latest in category wins);
                        # "OTHER" codes key by value so distinct ones accumulate
                        value = sgr_match.group(name)
                        key = value if name == "OTHER" else name.encode()
                        self.sgr_codes.pop(key, None)  # reorder to latest
                        self.sgr_codes[key] = value
            elif dec_save == b"7":
                self._sgr_save_dec = {**self.sgr_codes}
            elif dec_save == b"8":
                self.sgr_codes = {**self._sgr_save_dec}
            elif xt_push in (b"{", b"p"):
                self._sgr_save_xterm.append({**self.sgr_codes})
            elif xt_push in (b"}", b"q"):
                if self._sgr_save_xterm:
                    self.sgr_codes = self._sgr_save_xterm.pop()

    def get_escape(self) -> bytes:
        """Returns an escape code to restore previously accumulated state."""

        return b"\x1b[;" + b";".join(self.sgr_codes.values()) + b"m"
