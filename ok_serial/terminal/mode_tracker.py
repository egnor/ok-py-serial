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
    b"<(?P<kittypop>[0-9]*)u|"  # kitty keyboard: pop flag entries
    b">(?P<kittypush>[0-9]*)u|"  # kitty keyboard: push flag entry
    b"=(?P<kittyset>[0-9]*;?[0-9]*)u|"  # kitty keyboard: set current flags
    b"(?P<sgr>[0-9;:]*m)|"  # Set Graphics Rendition ('m' kept as terminator)
    b"(?P<sm>[0-9;]*)h|"  # ANSI mode set
    b"(?P<rm>[0-9;]*)l|"  # ANSI mode reset
    b">(?P<xtmodkeys>[0-9;]*)m|"  # XTerm set key modifier option
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
    - Character set designations (G0-G3) and GL locking shifts (SI/SO/LS2/LS3)
    - ANSI standard modes (SM/RM, CSI Pm h/l), eg. insert and newline modes
    - DEC private modes (DECSET/DECRST, CSI ? Pm h/l), eg. cursor visibility,
      auto-wrap, mouse reporting, alternate screen, bracketed paste
    - DEC other modes, eg. application keypad, cursor style, LEDs, protection
    - DEC mode save/restore (DECSC/DECRC)
    - Terminal resets (DECSTR, RIS)
    - XTerm save/restore (XTSAVE/XTRESTORE, XTPUSHSGR/XTPOPSGR)
    - XTerm pointer visibility mode (XTSMPOINTER)
    - XTerm key modifier options (XTMODKEYS, eg. modifyOtherKeys)
    - Kitty keyboard protocol flags (CSI >/</= ... u)

    Does not capture:
    - Cursor position, scrolling, or other non-mode-setting codes
    - Modes without restorable boolean semantics (see _DEC_MODE_ALIASES)
    - Other: single shifts, GR locking shifts, window title, palettes, ...

    State starts with explicit defaults and returns there on reset,
    which may not exactly match the terminal's own defaults after reset.
    State is normalized as much as possible for efficient diffing.

    Attributes:
    - ansi_modes: dict[int, bytes] from ANSI mode number to b"l" or b"h"
    - dec_modes: dict[int, bytes] from DEC mode number to b"l" or b"h"
    - dec_save_dec: dict[int, bytes] dec_modes saved by DECSC
    - dec_save_other: dict[str, bytes] other_modes saved by DECSC
    - dec_save_sgr: dict[str, bytes] sgr_codes saved by DECSC
    - kitty_key_flags: (list[int], list[int]) kitty stacks for main/alt screens
    - other_modes: dict[str, bytes] from type to escape code for other state
    - sgr_codes: dict[str, bytes] from SGR category name to latest value
    - xterm_save_dec: dict[int, bytes] dec_modes saved by XTSAVE
    - xterm_save_sgr: list[dict[str, bytes]] stack of sgr_codes from XTPUSHSGR

    TODO:
    - track but do not replay modes like DECSTBM, etc.
    """

    ansi_modes: dict[int, Literal[b"l", b"h"]]
    dec_modes: dict[int, Literal[b"l", b"h"]]
    dec_save_dec: dict[int, Literal[b"l", b"h"]]
    dec_save_other: dict[str, bytes]
    dec_save_sgr: dict[str, bytes]
    kitty_key_flags: tuple[list[int], list[int]]
    other_modes: dict[str, bytes]
    sgr_codes: dict[str, bytes]
    xterm_save_dec: dict[int, Literal[b"l", b"h"]]
    xterm_save_sgr: list[dict[str, bytes]]

    def __init__(self) -> None:
        self.reset()

    def copy(self) -> "TerminalModeTracker":
        return copy.deepcopy(self)

    def __repr__(self) -> str:
        return f"TerminalModeTracker{self.mode_chunks()!r}"

    def reset(self) -> None:
        """Returns all tracked state to the explicit baseline (as RIS does)."""
        self.ansi_modes = dict(_RESET_ANSI_MODES)
        self.dec_modes = dict(_RESET_DEC_MODES)
        self.dec_save_dec = {}
        self.dec_save_other = {}
        self.dec_save_sgr = {}
        self.kitty_key_flags = ([0], [0])
        self.other_modes = dict(_RESET_OTHER_MODES)
        self.sgr_codes = dict(_RESET_SGR_CODES)
        self.xterm_save_dec = {}
        self.xterm_save_sgr = []

        self._set_dec_mode(1048, b"h")  # put reset state in .dec_save_*

    def add_chunk(self, chunk: bytes | str) -> None:
        """Incorporates a chunk (from TerminalChunker) into saved state."""

        if not (rxm := isinstance(chunk, bytes) and _CODE_RX.fullmatch(chunk)):
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
        elif code == "kittypop":
            active_stack = self.kitty_key_flags[self.dec_modes.get(47) == b"h"]
            if count := int(body or 1):
                active_stack[:] = active_stack[:-count] or [0]
        elif code == "kittypush":
            active_stack = self.kitty_key_flags[self.dec_modes.get(47) == b"h"]
            active_stack.append(int(body or 0))
        elif code == "kittyset":
            kitty_params = (*body.split(b";"), b"")
            flags, op = int(kitty_params[0] or 0), int(kitty_params[1] or 1)
            active_stack = self.kitty_key_flags[self.dec_modes.get(47) == b"h"]
            if op == 1:  # set all flags
                active_stack[-1] = flags
            elif op == 2:  # set given bits
                active_stack[-1] |= flags
            elif op == 3:  # clear given bits
                active_stack[-1] &= ~flags
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
        elif code == "xtmodkeys":
            xtm_params = [int(p) for p in body.split(b";") if p.isdigit()]
            if not xtm_params:  # CSI > m resets all key modifier options
                ks = [k for k in self.other_modes if k.startswith("xtmodkeys")]
                for key in ks:
                    self.other_modes.pop(key)
            elif len(xtm_params) == 1:  # CSI > Pp m resets one option
                self.other_modes.pop(f"xtmodkeys{xtm_params[0]}", None)
            else:
                key = f"xtmodkeys{xtm_params[0]}"
                self.other_modes.pop(key, None)  # reorder to latest
                self.other_modes[key] = b"\x1b[>%d;%dm" % tuple(xtm_params[:2])
        elif code == "xtpopsgr":
            if self.xterm_save_sgr:
                self.sgr_codes = self.xterm_save_sgr.pop()
        elif code == "xtpushsgr":
            self.xterm_save_sgr.append({**self.sgr_codes})
        elif code in ("xtrestore", "xtsave"):
            aliases: list[int] = []
            for mode in (int(m) for m in body.split(b";") if m.isdigit()):
                mode_aliases = _DEC_MODE_ALIASES.get(mode, [mode])
                aliases.extend(a for a in mode_aliases if isinstance(a, int))
            if code == "xtsave":
                for alias in aliases:
                    self.xterm_save_dec.pop(alias, None)
                    if current_value := self.dec_modes.get(alias):
                        self.xterm_save_dec[alias] = current_value
            else:
                for alias in aliases:
                    self.dec_modes.pop(alias, None)
                    sv = self.xterm_save_dec.get(alias)
                    if restore_value := (sv or _RESET_DEC_MODES.get(alias)):
                        self._set_dec_mode(alias, restore_value)
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
                if (value := store.get(mode, b"l")) != base_store.get(mode):
                    if value == run_value:  # extend the run we just emitted
                        out[-1] = b"%s;%d%s" % (out[-1][:-1], mode, value)
                    else:
                        out.append(b"%s%d%s" % (prefix, mode, value))
                        run_value = value

        for mode, value in self.other_modes.items():
            if not (base and value == base.other_modes.get(mode)):
                out.append(value)

        # revert non-baseline other state (G2/G3, XTMODKEYS) only if modified
        if base:
            for mode in base.other_modes:
                if mode not in self.other_modes:
                    if revert := _REVERT_OTHER_MODES.get(mode):
                        out.append(revert)
                    elif mode.startswith("xtmodkeys"):
                        out.append(b"\x1b[>%dm" % int(mode[len("xtmodkeys") :]))

        # kitty keyboard flags: visit both windows, update stack as needed
        # (use self.dec_modes because DEC modes were updated above)
        mode_alt = now_alt = self.dec_modes.get(47) == b"h"
        for stack_alt in (False, True):
            stack = self.kitty_key_flags[stack_alt]
            base_stack = base.kitty_key_flags[stack_alt] if base else [0]
            if stack != base_stack:
                if now_alt != stack_alt:  # switch to window to edit stack
                    out.append(b"\x1b[?47%c" % b"lh"[now_alt := stack_alt])
                if stack[:-1] == base_stack[:-1]:  # tweak the top item
                    out.append(b"\x1b[=%du" % stack[-1])
                else:  # just rebuild the whole stack
                    if len(base_stack) > 1:
                        out.append(b"\x1b[<%du" % (len(base_stack) - 1))
                    if stack[0] != base_stack[0]:
                        out.append(b"\x1b[=%du" % stack[0])
                    for value in stack[1:]:
                        out.append(b"\x1b[>%du" % value)

        if now_alt != mode_alt:  # restore window state if needed
            out.append(b"\x1b[?47%c" % b"lh"[mode_alt])

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
                self.dec_save_sgr = {**self.sgr_codes}
                self.dec_save_dec = {
                    dm: self.dec_modes[dm] for dm in _DECSC_DEC_MODES
                }
                self.dec_save_other = {
                    om: ov
                    for om in _DECSC_OTHER_MODES
                    if (ov := self.other_modes.get(om))
                }
            elif (alias, value) == ("decsave", b"l"):
                self.sgr_codes = {**self.dec_save_sgr}
                for dm in _DECSC_DEC_MODES:
                    self.dec_modes.pop(dm, None)  # reorder to latest
                self.dec_modes.update(self.dec_save_dec)
                for om in _DECSC_OTHER_MODES:
                    self.other_modes.pop(om, None)  # clear / reorder to latest
                self.other_modes.update(self.dec_save_other)
            else:
                assert False, (mode, alias, value)
