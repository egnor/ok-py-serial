import copy
import re
from typing import Literal

# Baseline reset state assumed at startup or after full-reset (RIS)
_RESET_SGR_CODES = {"RESET": b""}  # plain SGR reset (CSI m)

_RESET_DEC_MODES: dict[int, Literal[b"l", b"h"]] = {
    # "l" modes first for run-efficiency
    1: b"l",  # DECCKM: normal (not application) cursor keys
    6: b"l",  # DECOM: absolute (not origin-relative) addressing
    9: b"l",  # X10 mouse reporting off
    47: b"l",  # alternate screen off (legacy variant)
    66: b"l",  # DECNKM: numeric (not application) keypad
    1000: b"l",  # mouse click reporting off
    1001: b"l",  # mouse highlight tracking off
    1002: b"l",  # mouse click-and-drag reporting off
    1003: b"l",  # mouse any-motion reporting off
    1004: b"l",  # focus in/out reporting off
    1005: b"l",  # UTF-8 mouse encoding off
    1006: b"l",  # SGR mouse encoding off
    1015: b"l",  # urxvt mouse encoding off
    1016: b"l",  # SGR-pixel mouse encoding off
    2004: b"l",  # bracketed paste off
    2026: b"l",  # synchronized output off (don't leave updates frozen!)
    # "h" modes next
    7: b"h",  # DECAWM: auto-wrap on
    25: b"h",  # DECTCEM: cursor visible
}

_RESET_ANSI_MODES: dict[int, Literal[b"l", b"h"]] = {
    2: b"l",  # KAM: keyboard unlocked
    4: b"l",  # IRM: replace (not insert) mode
    12: b"l",  # SRM: local echo off
    20: b"l",  # LNM: linefeed does not imply carriage return
}

_RESET_OTHER_MODES = {
    "decll0": b"\x1b[0q",  # all keyboard LEDs off
    "decsace": b"\x1b[0*x",  # stream mode for DECCARA / DECRARA
    "decsca": b'\x1b[0"q',  # character protection off
    "decscusr": b"\x1b[0 q",  # cursor style = terminal default
    "G0": b"\x1b(B",  # G0 charset = US-ASCII
    "G1": b"\x1b)B",  # G1 charset = US-ASCII
    # Omit G2 and G3 here; see _REVERT_OTHER_MODES below
    "keypad": b"\x1b>",  # numeric keypad (DECKPNM)
    "shift": b"\x0f",  # SI: GL = G0
    "xtsmpointer": b"\x1b[>1p",  # pointer hidden while typing (xterm default)
}

# These are NOT part of initial reset, only used when set in a prior state
_REVERT_OTHER_MODES = {
    # G2/G3 support and defaults vary by terminal
    "G2": b"\x1b*B",  # G2 charset = US-ASCII
    "G3": b"\x1b+B",  # G3 charset = US-ASCII
}

# regexp to match relevant terminal escape sequences
# https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
_CODE_RX = re.compile(
    # single-byte codes
    b"(?P<shift>[\x0e\x0f]|\x1b[no])|"  # GL locking shift: SI/SO/LS2/LS3
    # ESC codes
    b"\x1b(?:"
    b"(?P<charset>[\x28-\x2b\x2e\x2f\x2d][\x20-\x2f]*[\x30-\x7e])|"
    b"(?P<decrc>)8|"  # DEC Restore Cursor (and attributes)
    b"(?P<decsc>)7|"  # DEC Save Cursor (and attributes)
    b"(?P<keypad>[=>])|"  # application (=) / numeric (>) keypad
    b"(?P<ris>)c"  # Reset to Initial State (RIS), a full reset
    b")|"
    # CSI codes
    b"(?:\x1b\\[|\x9b)(?:"
    b"(?P<decll>2?[0-9])q|"  # DEC load LEDs
    b"\\?(?P<decrst>[0-9;]*)l|"  # DEC mode reset
    b"(?P<decsace>[0-9])\\*x|"  # DEC select attribute change extent
    b'(?P<decsca>[0-9])"q|'  # DEC set character protection attribute
    b"(?P<decscusr>[0-9]) q|"  # DEC set cursor style
    b"\\?(?P<decset>[0-9;]*)h|"  # DEC mode set
    b"(?P<decstr>)!p|"  # Soft terminal reset
    b"(?P<sgr>.*m)|"  # Set Graphics Rendition (capture 'm' as terminator)
    b"(?P<sm>[0-9;]*)h|"  # ANSI mode set
    b"(?P<rm>[0-9;]*)l|"  # ANSI mode reset
    b">(?P<xtsmpointer>[0-9])p|"  # XTerm pointer visibility
    b"#(?P<xtpopsgr>[}q])|"  # XTerm Pop SGR state
    b"#(?P<xtpushsgr>[{p])|"  # XTerm Push SGR state
    b"\\?(?P<xtrestore>[0-9;]*)r|"  # XTerm restore DEC modes
    b"\\?(?P<xtsave>[0-9;]*)s"  # XTerm save DEC modes
    b")"
)

# regexp to match individual SGR content codes, each ending in ; or m
# codes in the same category supercede (latest wins)
_SGR_CODE_RX = re.compile(
    b"(?:"
    b"(?P<RESET>0?)|"  # 0 or empty parameter: reset all attributes
    b"(?P<weight>1|2|22)|"  # bold / faint / normal intensity
    b"(?P<slant>3|20|23)|"  # italic / Fraktur / neither (23 cancels both)
    b"(?P<under>4(?::[0-9]+)?|21|24)|"  # single (or 4:n styled) / double / off
    b"(?P<blink>5|6|25)|"  # slow / rapid / off
    b"(?P<inverse>7|27)|"  # reverse video / off
    b"(?P<hidden>8|28)|"  # conceal / reveal
    b"(?P<strike>9|29)|"  # crossed-out / off
    b"(?P<font>1[0-9])|"  # primary (10) or alternative (11-19) font
    b"(?P<prop>26|50)|"  # proportional spacing on / off
    b"(?P<frame>5[12]|54)|"  # framed / encircled / off
    b"(?P<over>53|55)|"  # overlined / off
    b"(?P<ideoline>6[0-9])|"  # CJK side/under/over lines (60-64) / off (65)
    b"(?P<fg>3[0-79]|9[0-9]|38;5;[0-9]+|38;2;[0-9]+;[0-9]+;[0-9]+|38:[0-9:]*)|"
    b"(?P<bg>4[0-79]|10[0-9]|48;5;[0-9]+|48;2;[0-9]+;[0-9]+;[0-9]+|48:[0-9:]*)|"
    b"(?P<ul>58;5;[0-9]+|58;2;[0-9]+;[0-9]+;[0-9]+|58:[0-9:]*|59)|"
    b"(?P<baseline>7[0-9])|"  # super- / sub-script / off
    b"(?P<OTHER>[0-9:]+)"
    b")[;m]"
)

# Escape codes that can be directly captured as a single mode
_SIMPLE_CODES = "decsace decsca decscusr keypad shift xtsmpointer".split()

# DEC private modes to track & restore using different DEC modes
_DEC_MODE_ALIASES: dict[int, list[int | Literal["decsave"]]] = {
    2: [],  # DECANM: VT52 mode, not supported
    3: [],  # DECCOLM: 132-column mode, clears screen; not supported
    1047: [47],  # xterm variant of DEC alternate screen with clear on exit
    1048: ["decsave"],  # xterm variant of DECSC/DECRC
    1049: [47, "decsave"],  # xterm combo 1047 + 1048
}

# Modes saved by DECSC (restored by DECRC)
_DECSC_DEC_MODES = [6]
_DECSC_OTHER_MODES = ["decsca", "G0", "G1", "G2", "G3", "shift"]

# Modes reset by DECSTR, restored to baseline (or dropped) when DECSTR is seen.
# Excludes DECAWM (?7) which terminals vary on.
_DECSTR_DEC_MODES = [1, 6, 25, 66]  # DECCKM, DECOM, DECTCEM, DECNKM
_DECSTR_ANSI_MODES = [2, 4]  # KAM, IRM
_DECSTR_OTHER_MODES = ["decsca", "G0", "G1", "G2", "G3", "keypad", "shift"]

# DEC private mode families which can have at most one member set
_ONEHOT_DEC_MODE_SETS = (
    frozenset({9, 1000, 1001, 1002, 1003}),  # mouse protocol selection
    frozenset({1005, 1006, 1015, 1016}),  # mouse coordinate encoding
)


class TerminalModeTracker:
    """Buffered VTxxx settings for interpretation and restoration.

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
    - Modes without restorable boolean semantics (see _DEC_MODE_ALIASES)
    - Single shifts, GR locking shifts, modifyOtherKeys / kitty keyboard,
      window title, palettes, or other esoteric state

    State starts with explicit defaults and returns there on reset,
    which may not exactly match the terminal's own defaults after reset.

    State is normalized where modes interact, so equal dict values imply
    equal terminal state (which mode_chunks diffing relies on): shared
    register DEC mode families (mouse protocol/encoding) keep at most one
    member set, and keyboard LEDs are either all-off (decll0) or all
    tracked individually (decll1-3).

    Attributes:
    - sgr_codes: dict[str, bytes] from SGR category name to latest value
    - ansi_modes: dict[int, bytes] from ANSI mode number to b"l" or b"h"
    - dec_modes: dict[int, bytes] from DEC mode number to b"l" or b"h"
    - other_modes: dict[str, bytes] from type to escape code for other state

    TODO:
    - track but do not replay modes like DECSTBM, etc.
    """

    sgr_codes: dict[str, bytes]
    ansi_modes: dict[int, Literal[b"l", b"h"]]
    dec_modes: dict[int, Literal[b"l", b"h"]]
    other_modes: dict[str, bytes]
    _dec_save_dec: dict[int, Literal[b"l", b"h"]]  # DEC(SC/RC) saved modes
    _dec_save_other: dict[str, bytes]  # DEC(SC/RC) saved other state
    _dec_save_sgr: dict[str, bytes]  # DEC(SC/RC) saved SGM codes
    _xterm_save_dec: dict[int, Literal[b"l", b"h"]]  # XT(SAVE/RESTORE)
    _xterm_save_sgr: list[dict[str, bytes]]  # XT(PUSH/POP)SGR

    def __init__(self) -> None:
        self.reset()

    def copy(self) -> "TerminalModeTracker":
        return copy.deepcopy(self)

    def __repr__(self) -> str:
        return f"TerminalModeTracker{self.mode_chunks()!r}"

    def reset(self) -> None:
        """Returns all tracked state to the explicit baseline (as RIS does)."""
        self.sgr_codes = dict(_RESET_SGR_CODES)
        self.ansi_modes = dict(_RESET_ANSI_MODES)
        self.dec_modes = dict(_RESET_DEC_MODES)
        self.other_modes = dict(_RESET_OTHER_MODES)
        self._dec_save_dec = {}
        self._dec_save_other = {}
        self._dec_save_sgr = {}
        self._xterm_save_dec = {}
        self._xterm_save_sgr = []

        self._set_dec_mode(1048, b"h")  # put reset state in ._dec_save_*

    def add_chunk(self, chunk: bytes | str) -> None:
        """Incorporates a chunk (from TerminalChunker) into saved state."""

        if not (
            isinstance(chunk, bytes) and (rxm := _CODE_RX.fullmatch(chunk))
        ):
            return

        code = rxm.lastgroup
        assert code, rxm.groupdict()
        body = rxm[code]

        # Simple modes to save with no further processing
        if code in _SIMPLE_CODES:
            self.other_modes.pop(code, None)  # reorder to latest
            self.other_modes[code] = chunk

        # ESC codes
        elif code == "charset":
            key = f"G{body[0] & 3:d}"  # G0-G3 charsets
            self.other_modes.pop(key, None)  # reorder to latest
            self.other_modes[key] = chunk
        elif code == "decrc":
            self._set_dec_mode(1048, b"l")  # handle DECRC via xterm code
        elif code == "decsc":
            self._set_dec_mode(1048, b"h")  # handle DECSC via xterm code
        elif code == "ris":  # full reset; replay baseline (not a clear!)
            self.reset()

        # CSI codes
        elif code == "decll":
            led = (param := int(body)) % 10
            key = f"decll{led}"
            if led == 0:  # single all-off code
                for n in range(1, 10):
                    self.other_modes.pop(f"decll{n}", None)
            elif self.other_modes.pop("decll0", None):  # decompose all-off
                for n in (1, 2, 3):
                    self.other_modes[f"decll{n}"] = b"\x1b[2%dq" % n
            self.other_modes.pop(key, None)  # reorder to latest
            self.other_modes[key] = b"\x1b[%dq" % param  # canonical form
        elif code in ("decrst", "decset"):
            dec_value: Literal[b"l", b"h"] = b"h" if code == "decset" else b"l"
            for mode in (int(m) for m in body.split(b";") if m.isdigit()):
                self._set_dec_mode(mode, dec_value)
        elif code == "decstr":  # soft reset: governed state to baseline
            self.sgr_codes = dict(_RESET_SGR_CODES)
            for dec_mode in _DECSTR_DEC_MODES:
                self.dec_modes[dec_mode] = _RESET_DEC_MODES[dec_mode]
            for ansi_mode in _DECSTR_ANSI_MODES:
                self.ansi_modes[ansi_mode] = _RESET_ANSI_MODES[ansi_mode]
            for other_mode in _DECSTR_OTHER_MODES:
                if reset_value := _RESET_OTHER_MODES.get(other_mode):
                    self.other_modes[other_mode] = reset_value
                else:
                    self.other_modes.pop(other_mode, None)
            self._set_dec_mode(1048, b"h")  # DECSTR clears DECSC state too
        elif code == "sgr":
            sgr_pos = 0
            while sgr_pos < len(body):
                sgr_rxm = _SGR_CODE_RX.match(body, sgr_pos)
                assert sgr_rxm, body[sgr_pos:]
                sgr, sgr_pos = sgr_rxm.lastgroup, sgr_rxm.end()
                assert sgr, sgr_rxm.groupdict()
                sgr_body = sgr_rxm[sgr]
                if sgr == "RESET":
                    self.sgr_codes = {"RESET": b""}
                else:
                    key = sgr_body.decode() if sgr == "OTHER" else sgr
                    self.sgr_codes.pop(key, None)  # reorder to latest
                    self.sgr_codes[key] = sgr_body
        elif code in ("rm", "sm"):
            ansi_value: Literal[b"l", b"h"] = b"h" if code == "sm" else b"l"
            for mode in (int(m) for m in body.split(b";") if m.isdigit()):
                self.ansi_modes.pop(mode, None)  # reorder to latest
                self.ansi_modes[mode] = ansi_value
        elif code == "xtpopsgr":
            if self._xterm_save_sgr:
                self.sgr_codes = self._xterm_save_sgr.pop()
        elif code == "xtpushsgr":
            self._xterm_save_sgr.append({**self.sgr_codes})
        elif code in ("xtrestore", "xtsave"):
            aliases: list[int] = []
            for mode in (int(m) for m in body.split(b";") if m.isdigit()):
                mode_aliases = _DEC_MODE_ALIASES.get(mode, [mode])
                aliases.extend(a for a in mode_aliases if isinstance(a, int))
            if code == "xtsave":
                for alias in aliases:
                    self._xterm_save_dec.pop(alias, None)
                    if current_value := self.dec_modes.get(alias):
                        self._xterm_save_dec[alias] = current_value
            else:
                for alias in aliases:
                    self.dec_modes.pop(alias, None)
                    sv = self._xterm_save_dec.get(alias)
                    if value := sv or _RESET_DEC_MODES.get(alias):
                        self._set_dec_mode(alias, value)
        else:
            assert False, code  # unknown named group?

    def mode_chunks(
        self, *, base: "TerminalModeTracker | None" = None
    ) -> list[bytes]:
        """Returns escape code(s) that encode the accumulated state.
        - base: if not None, output diffs from this base state
        """

        # Note, DECSTR would reset scrolling margins, etc; avoid it
        out: list[bytes] = []

        # Emit full SGR if different from base, instead of trying to be clever
        if self.sgr_codes != (base.sgr_codes if base else {}):
            out.append(b"\x1b[" + b";".join(self.sgr_codes.values()) + b"m")

        for prefix, attr in (b"\x1b[?", "dec_modes"), (b"\x1b[", "ansi_modes"):
            store = getattr(self, attr)
            base_store = getattr(base, attr) if base else {}
            run_value = None  # reset per store; the two never merge
            for mode in {**store, **base_store}:  # union of keys
                value, base_value = store.get(mode, b"l"), base_store.get(mode)
                if value == base_value:
                    pass
                elif value == run_value:  # extend the run we just emitted
                    out[-1] = b"%s;%d%s" % (out[-1][:-1], mode, value)
                else:
                    out.append(b"%s%d%s" % (prefix, mode, value))
                    run_value = value

        for mode, value in self.other_modes.items():
            if not (base and value == base.other_modes.get(mode)):
                out.append(value)

        # restore G2/G3 to ASCII only if it had been modified
        if base:
            for mode, revert in _REVERT_OTHER_MODES.items():
                if mode in base.other_modes and mode not in self.other_modes:
                    out.append(revert)

        return out

    def _set_dec_mode(self, mode: int, value: Literal[b"l", b"h"]) -> None:
        """Records a DEC mode value with aliasing, onehot, etc."""
        for alias in _DEC_MODE_ALIASES.get(mode, [mode]):
            if isinstance(alias, int):
                for onehot_set in _ONEHOT_DEC_MODE_SETS:
                    if alias in onehot_set:
                        self.dec_modes.update((m, b"l") for m in onehot_set)
                self.dec_modes.pop(alias, None)  # reorder to latest
                self.dec_modes[alias] = value
            elif (alias, value) == ("decsave", b"h"):
                self._dec_save_sgr = {**self.sgr_codes}
                self._dec_save_dec = {
                    dm: self.dec_modes[dm] for dm in _DECSC_DEC_MODES
                }
                self._dec_save_other = {
                    om: ov
                    for om in _DECSC_OTHER_MODES
                    if (ov := self.other_modes.get(om))
                }
            elif (alias, value) == ("decsave", b"l"):
                self.sgr_codes = {**self._dec_save_sgr}
                for dm in _DECSC_DEC_MODES:
                    self.dec_modes.pop(dm, None)  # reorder to latest
                self.dec_modes.update(self._dec_save_dec)
                for om in _DECSC_OTHER_MODES:
                    self.other_modes.pop(om, None)  # clear / reorder to latest
                self.other_modes.update(self._dec_save_other)
            else:
                assert False, (mode, alias, value)
