import re

# regexp to match relevant terminal escape sequences
# https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
ESCAPE_CODE_RX = re.compile(
    # single-byte codes
    b"(?P<shift>[\x0e\x0f]|\x1b[no])|"  # GL locking shift: SI/SO/LS2/LS3
    # ESC codes
    b"\x1b(?:"
    b"(?P<charset>[\x28-\x2b\x2e\x2f\x2d][\x20-\x2f]*[\x30-\x7e])|"
    b"(?P<decrc>8)|"  # DEC Restore Cursor (and attributes)
    b"(?P<decsc>7)|"  # DEC Save Cursor (and attributes)
    b"(?P<keypad>[=>])|"  # application (=) / numeric (>) keypad
    b"(?P<ris>c)"  # Reset to Initial State (RIS), a full reset
    b")|"
    # CSI codes
    b"(?:\x1b\\[|\x9b)(?:"
    b"(?P<decll>2?\\d)q|"  # DEC load LEDs
    b"\\?(?P<decrst>[\\d;]*)l|"  # DEC mode reset
    b'(?P<decsca>\\d)"q|'  # DEC set character protection attribute
    b"(?P<decscusr>\\d) q|"  # DEC set cursor style
    b"\\?(?P<decset>[\\d;]*)h|"  # DEC mode set
    b"(?P<decstr>!)p|"  # Soft terminal reset
    b"(?P<sgr>.*m)|"  # Set Graphics Rendition
    b"(?P<sm>[\\d;]*)h|"  # ANSI mode set
    b"(?P<rm>[\\d;]*)l|"  # ANSI mode reset
    b">(?P<xtsmpointer>\\d)p|"  # XTerm pointer visibility
    b"#(?P<xtpopsgr>[}q])|"  # XTerm Pop SGR state
    b"#(?P<xtpushsgr>[{p])|"  # XTerm Push SGR state
    b"\\?(?P<xtrestore>[\\d;]*)r|"  # XTerm restore DEC modes
    b"\\?(?P<xtsave>[\\d;]*)s"  # XTerm save DEC modes
    b")"
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
    b"(?P<ideoline>6[0-5])|"  # CJK side/under/over lines (60-64) / off (65)
    b"(?P<fg>3[0-79]|9[0-7]|38;5;\\d+|38;2;\\d+;\\d+;\\d+|38:[\\d:]*)|"
    b"(?P<bg>4[0-79]|10[0-7]|48;5;\\d+|48;2;\\d+;\\d+;\\d+|48:[\\d:]*)|"
    b"(?P<ul>58;5;\\d+|58;2;\\d+;\\d+;\\d+|58:[\\d:]*|59)|"  # underline color
    b"(?P<baseline>7[345])|"  # super- / sub-script / off
    b"(?P<OTHER>[\\d:]+)"
    b")[m;]"
)

# DEC private modes (CSI ? Pm h/l) we do NOT capture for replay:
# - 2 (DECANM): reset switches to VT52 mode and changes the escape grammar
# - 3 (DECCOLM): set/reset clears the screen and resizes as a side effect
# - 1048: an action (save/restore cursor), not a state
SKIP_DEC_MODES = frozenset({2, 3, 1048})

# Modes reset by DECSTR. Excludes DECAWM (?7) which terminals vary on.
DECSTR_DEC_MODES = frozenset({1, 6, 25, 66})  # DECCKM, DECOM, DECTCEM, DECNKM
DECSTR_ANSI_MODES = frozenset({2, 4})  # KAM, IRM
DECSTR_OTHER_PREFIXES = frozenset({"charset", "decsca", "keypad", "shift"})


class TerminalModeSaver:
    """Buffers VTxxx settings for interpretation and restoration.

    Currently captures:
    - SGR codes (text style, color, font, etc.), including save/restore
    - DEC private modes (DECSET/DECRST, CSI ? Pm h/l), e.g. cursor visibility,
      auto-wrap, mouse reporting, alternate screen, bracketed paste
    - ANSI standard modes (SM/RM, CSI Pm h/l), e.g. insert and newline modes
    - DEC mode save/restore (XTSAVE/XTRESTORE) and terminal resets (DECSTR, RIS)
    - Character set designations (G0-G3) and GL locking shifts (SI/SO/LS2/LS3)
    - Application vs. numeric keypad mode (DECKPAM/DECKPNM)
    - Cursor style (DECSCUSR), keyboard LEDs (DECLL), character protection
      (DECSCA), and xterm pointer visibility mode (XTSMPOINTER)

    Does not capture:
    - Cursor position, scrolling, or other non-mode-setting codes
    - Modes without restorable boolean semantics (see SKIP_DEC_MODES)
    - GR locking shifts/single shifts, modifyOtherKeys / kitty keyboard,
      window title, palettes, or other esoteric state

    Attributes:
    - soft_reset: bool, True if DECSTR or RIS was observed
    - sgr_codes: dict[str, bytes] from SGR category name to latest value
    - ansi_modes: dict[int, bytes] from ANSI mode number to b"l" or b"h"
    - dec_modes: dict[int, bytes] from DEC mode number to b"l" or b"h"
    - other_modes: dict[str, bytes] from type to escape code for other state
    """

    def __init__(self) -> None:
        self.soft_reset = False  # replay DECSTR before the rest of state
        self.sgr_codes: dict[str, bytes] = {}
        self.ansi_modes: dict[int, bytes] = {}
        self.dec_modes: dict[int, bytes] = {}
        self.other_modes: dict[str, bytes] = {}
        self._sgr_save_dec: dict[str, bytes] = {}  # DECSC/DECRC
        self._sgr_save_xterm: list[dict[str, bytes]] = []  # XT(PUSH/POP)SGR
        self._dec_save: dict[int, bytes] = {}  # XTSAVE/XTRESTORE

    def add_escape(self, chunk: bytes) -> None:
        """Incorporates an escape (from TerminalChunker) into saved state."""

        if escape_match := ESCAPE_CODE_RX.fullmatch(chunk):
            escape = escape_match.lastgroup
            assert escape, escape_match.group()
            body = escape_match[escape]

            # Simple modes to save
            if escape in (
                "decsca",
                "decscusr",
                "keypad",
                "shift",
                "xtsmpointer",
            ):
                self.other_modes.pop(escape, None)  # reorder to latest
                self.other_modes[escape] = chunk

            # ESC codes
            elif escape == "charset":
                key = f"charset:{body[0] & 3:d}"  # G0-G3
                self.other_modes.pop(key, None)  # reorder to latest
                self.other_modes[key] = chunk
            elif escape == "decrc":
                self.sgr_codes = {**self._sgr_save_dec}
            elif escape == "decsc":
                self._sgr_save_dec = {**self.sgr_codes}
            elif escape == "ris":  # full reset & clear screen; replay DECSTR
                # TODO: theoretically need explicit reset for non-DECSTR items
                self.soft_reset = True
                self.sgr_codes.clear()
                self.dec_modes.clear()
                self.ansi_modes.clear()
                self.other_modes.clear()
                self._dec_save.clear()
                self._sgr_save_dec.clear()
                self._sgr_save_xterm.clear()

            # CSI codes
            elif escape == "decll":
                if body == b"0":
                    [self.other_modes.pop(f"decll:{n}", None) for n in "123"]
                key = f"decll:{body[-1]:c}"  # decll:[1-3] or decll:0 (reset)
                self.other_modes.pop(key, None)  # reorder to latest
                self.other_modes[key] = chunk
            elif dec_value := {"decrst": b"l", "decset": b"h"}.get(escape):
                for mode in (int(m) for m in body.split(b";") if m.isdigit()):
                    if mode not in SKIP_DEC_MODES:
                        self.dec_modes.pop(mode, None)  # reorder to latest
                        self.dec_modes[mode] = dec_value
            elif escape == "decstr":  # soft reset: replay it, then later deltas
                self.soft_reset = True
                self.sgr_codes.clear()  # the replayed DECSTR resets SGR for us
                for k in list(self.other_modes.keys()):
                    if k.split(":", 1)[0] in DECSTR_OTHER_PREFIXES:
                        del self.other_modes[k]
                for mode in DECSTR_DEC_MODES:
                    self.dec_modes.pop(mode, None)
                for mode in DECSTR_ANSI_MODES:
                    self.ansi_modes.pop(mode, None)
            elif escape == "sgr":
                sgr_pos = 0
                while sgr_pos < len(body):
                    code_match = SGR_SUBCODE_RX.match(body, sgr_pos)
                    assert code_match, body[sgr_pos:]
                    code, sgr_pos = code_match.lastgroup, code_match.end()
                    assert code, code_match.group()
                    code_body = code_match[code]
                    if code == "RESET":
                        self.sgr_codes = {"RESET": code_body}
                    else:
                        key = code_body.decode() if code == "OTHER" else code
                        self.sgr_codes.pop(key, None)  # reorder to latest
                        self.sgr_codes[key] = code_body
            elif ansi_value := {"rm": b"l", "sm": b"h"}.get(escape):
                for mode in (int(m) for m in body.split(b";") if m.isdigit()):
                    self.ansi_modes.pop(mode, None)  # reorder to latest
                    self.ansi_modes[mode] = ansi_value
            elif escape == "xtpopsgr":
                if self._sgr_save_xterm:
                    self.sgr_codes = self._sgr_save_xterm.pop()
            elif escape == "xtpushsgr":
                self._sgr_save_xterm.append({**self.sgr_codes})
            elif escape == "xtrestore":
                for mode in (int(m) for m in body.split(b";") if m.isdigit()):
                    self.dec_modes.pop(mode, None)  # reorder to latest
                    if saved_value := self._dec_save.get(mode):
                        self.dec_modes[mode] = saved_value
            elif escape == "xtsave":
                for mode in (int(m) for m in body.split(b";") if m.isdigit()):
                    self._dec_save.pop(mode, None)
                    if current_value := self.dec_modes.get(mode):
                        self._dec_save[mode] = current_value
            else:
                assert False, escape  # one named group should match

    def get_restore_escapes(self) -> bytes:
        """Returns escape code(s) to restore previously accumulated state."""

        out: list[bytes] = []
        if self.soft_reset:
            out.append(b"\x1b[!p")  # DECSTR: re-establish known baseline
        if self.sgr_codes:
            out.append(b"\x1b[" + b";".join(self.sgr_codes.values()) + b"m")

        prefix_store = (b"\x1b[?", self.dec_modes), (b"\x1b[", self.ansi_modes)
        for prefix, store in prefix_store:
            run_suffix = None  # reset per store so the two never merge together
            for mode, suffix in store.items():
                if suffix == run_suffix:  # extend the run we just emitted
                    out[-1] = b"%s;%d%s" % (out[-1][:-1], mode, suffix)
                else:
                    out.append(b"%s%d%s" % (prefix, mode, suffix))
                    run_suffix = suffix

        out.extend(self.other_modes.values())
        return b"".join(out)
