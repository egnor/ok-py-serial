import re

# https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
STYLE_CODE_RX = re.compile(
    # group 1: Set Graphics Rendition (SGR) content
    b"(?:\x1b\\[|\x9b)(.*m)|"
    # group 2: DECSC / DECRC (DEC Save/Restore Cursor)
    b"\x1b([78])|"
    # group 3: XTPUSHSGR / XTPOPSGR (XTerm Push/Pop SGR)
    b"(?:\x1b\\[|\x9b)#([{}pq])"
)

SGR_CODE_RX = re.compile(
    b"(?:"
    b"(?P<reset>0?)|"
    b"(?P<weight>1|2|22)|"
    b"(?P<slant>3|23)|"
    b"(?P<under>4|21|24)|"
    b"(?P<blink>5|6|25)|"
    b"(?P<inverse>7|27)|"
    b"(?P<hidden>8|28)|"
    b"(?P<strike>9|29)|"
    b"(?P<fgcolor>3[0-79]|9[0-7]|38;2;\\d+;\\d+;\\d+|38:[\\d:]*)|"
    b"(?P<bgcolor>4[0-79]|10[0-7]|48;2;\\d+;\\d+;\\d+|48:[\\d:]*)|"
    b"(?P<other>[^m;])"
    b")[m;]"
)


class TerminalStyleBuffer:
    """Buffers VTxxx text-style codes for interpretation and restoration."""

    def __init__(self) -> None:
        self.codes: dict[bytes, bytes] = {}
        self._dec_save: dict[bytes, bytes] = {}
        self._xt_stack: list[dict[bytes, bytes]] = []

    def add_escape(self, chunk: bytes) -> None:
        """Incorporates a code (per TerminalChunker) if style-relevant"""

        if match := STYLE_CODE_RX.fullmatch(chunk):
            sgr, dec_save, xt_push = match.groups()
            if sgr is not None:
                pos = 0
                while pos < len(sgr):
                    sgr_match = SGR_CODE_RX.match(sgr, pos)
                    assert sgr_match, sgr[pos:]
                    # TODO: process value, advance pos
            elif dec_save == "7":
                self._dec_save = {**self.codes}
            elif dec_save == "8":
                self.codes = {**self._dec_save}
            elif xt_push in ("{", "p"):
                self._xt_stack.append({**self.codes})
            elif xt_push in ("}", "q"):
                if self._xt_stack:
                    self.codes = self._xt_stack.pop()
