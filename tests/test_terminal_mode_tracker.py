"""Unit tests for ok_serial._terminal_mode_tracker."""

from ok_serial._terminal_mode_tracker import (
    RESET_ANSI_MODES,
    RESET_DEC_MODES,
    RESET_OTHER_MODES,
    RESET_SGR_CODES,
    TerminalModeTracker,
)


def track(*escapes: bytes) -> TerminalModeTracker:
    """Feeds escapes to a fresh tracker and returns it for inspection."""
    tracker = TerminalModeTracker()
    for escape in escapes:
        tracker.add_chunk(escape)
    return tracker


def sgr(*escapes: bytes) -> dict[str, bytes]:
    return track(*escapes).sgr_codes


def dec(*escapes: bytes) -> dict[int, bytes]:
    return track(*escapes).dec_modes


def ansi(*escapes: bytes) -> dict[int, bytes]:
    return track(*escapes).ansi_modes


def other(*escapes: bytes) -> dict[str, bytes]:
    return track(*escapes).other_modes


def test_baseline_state():
    # a fresh tracker starts at the explicit baseline, not empty
    tracker = TerminalModeTracker()
    assert tracker.sgr_codes == RESET_SGR_CODES
    assert tracker.dec_modes == RESET_DEC_MODES
    assert tracker.ansi_modes == RESET_ANSI_MODES
    assert tracker.other_modes == RESET_OTHER_MODES
    assert tracker._save_sgr_dec == RESET_SGR_CODES
    assert tracker._save_sgr_xterm == []
    assert tracker._save_dec_xterm == {}


def test_single_attribute_kept():
    assert sgr(b"\x1b[1m") == {**RESET_SGR_CODES, "weight": b"1"}  # bold
    assert sgr(b"\x1b[31m") == {**RESET_SGR_CODES, "fg": b"31"}  # red fg


def test_combined_params_in_one_escape():
    # one CSI carrying several ;-separated codes
    expect = {**RESET_SGR_CODES, "weight": b"1", "fg": b"31"}
    assert sgr(b"\x1b[1;31m") == expect


def test_latest_in_category_wins():
    # intensity is one category: 22 (normal) supersedes 1 (bold)
    assert sgr(b"\x1b[1m", b"\x1b[22m") == {**RESET_SGR_CODES, "weight": b"22"}
    # foreground is one category: blue supersedes red
    assert sgr(b"\x1b[31m", b"\x1b[34m") == {**RESET_SGR_CODES, "fg": b"34"}


def test_independent_categories_coexist():
    out = sgr(b"\x1b[1m", b"\x1b[31m", b"\x1b[42m")
    assert out == {**RESET_SGR_CODES, "weight": b"1", "fg": b"31", "bg": b"42"}


def test_reset_clears_everything():
    assert sgr(b"\x1b[1;31m", b"\x1b[0m") == {"RESET": b"0"}
    # empty parameter defaults to 0 == reset (ECMA-48), so ESC[m clears too
    assert sgr(b"\x1b[1;31m", b"\x1b[m") == {"RESET": b""}
    # an empty param mid-sequence resets in place
    assert sgr(b"\x1b[31;;1m") == {"RESET": b"", "weight": b"1"}


def test_256_color_and_truecolor():
    assert sgr(b"\x1b[38;5;200m") == {  # indexed fg
        **RESET_SGR_CODES,
        "fg": b"38;5;200",
    }
    assert sgr(b"\x1b[48;2;10;20;30m") == {  # rgb bg
        **RESET_SGR_CODES,
        "bg": b"48;2;10;20;30",
    }


def test_overline_frame_and_other_cancel_groups():
    # each "off" code supersedes its "on" code instead of accumulating
    base = RESET_SGR_CODES
    assert sgr(b"\x1b[53m", b"\x1b[55m") == {**base, "over": b"55"}
    assert sgr(b"\x1b[51m", b"\x1b[52m") == {**base, "frame": b"52"}
    assert sgr(b"\x1b[52m", b"\x1b[54m") == {**base, "frame": b"54"}
    assert sgr(b"\x1b[11m", b"\x1b[13m") == {**base, "font": b"13"}
    assert sgr(b"\x1b[26m", b"\x1b[50m") == {**base, "prop": b"50"}
    assert sgr(b"\x1b[60m", b"\x1b[65m") == {**base, "ideoline": b"65"}
    assert sgr(b"\x1b[73m", b"\x1b[75m") == {**base, "baseline": b"75"}
    # 23 cancels both italic (3) and Fraktur (20): same category
    assert sgr(b"\x1b[20m", b"\x1b[23m") == {**base, "slant": b"23"}


def test_extension_color_and_styled_underline():
    # styled underline (kitty 4:3 = curly) is one category with 4/21/24
    assert sgr(b"\x1b[4:3m", b"\x1b[24m") == {**RESET_SGR_CODES, "under": b"24"}
    # underline color (T.416) is its own color category
    assert sgr(b"\x1b[58;5;9m") == {**RESET_SGR_CODES, "ul": b"58;5;9"}
    assert sgr(b"\x1b[58;2;1;2;3m", b"\x1b[59m") == {
        **RESET_SGR_CODES,
        "ul": b"59",
    }


def test_new_groups_do_not_shadow_colors():
    # codes that share a leading digit with the new groups still route right
    assert sgr(b"\x1b[100m") == {**RESET_SGR_CODES, "bg": b"100"}  # bright bg
    assert sgr(b"\x1b[44m") == {**RESET_SGR_CODES, "bg": b"44"}  # blue bg
    assert sgr(b"\x1b[5m") == {**RESET_SGR_CODES, "blink": b"5"}  # blink


def test_unknown_multidigit_code_accumulates():
    # genuinely unknown codes (56/57 are reserved) still accumulate by value
    expect = {**RESET_SGR_CODES, "56": b"56", "57": b"57"}
    assert sgr(b"\x1b[56m", b"\x1b[57m") == expect


def test_8bit_csi_introducer():
    # 0x9b is the single-byte CSI, equivalent to ESC [
    assert sgr(b"\x9b1m") == {**RESET_SGR_CODES, "weight": b"1"}
    assert dec(b"\x9b?25l") == {**RESET_DEC_MODES, 25: b"l"}
    assert ansi(b"\x9b4h") == {**RESET_ANSI_MODES, 4: b"h"}


def test_non_style_escapes_ignored():
    # cursor move and erase carry no style; state stays at baseline
    tracker = track(b"\x1b[2J", b"\x1b[H", b"\x1b[10;5H")
    assert tracker.sgr_codes == RESET_SGR_CODES
    assert tracker.dec_modes == RESET_DEC_MODES
    assert tracker.ansi_modes == RESET_ANSI_MODES
    assert tracker.other_modes == RESET_OTHER_MODES
    # ...and they don't disturb an existing style
    assert sgr(b"\x1b[1m", b"\x1b[2J") == {**RESET_SGR_CODES, "weight": b"1"}


def test_dec_save_and_restore():
    # ESC 7 snapshots the style, ESC 8 restores it
    expect = {**RESET_SGR_CODES, "weight": b"1", "fg": b"31"}
    assert sgr(b"\x1b[1m", b"\x1b7", b"\x1b[31m") == expect
    out = sgr(b"\x1b[1m", b"\x1b7", b"\x1b[31m", b"\x1b8")
    assert out == {**RESET_SGR_CODES, "weight": b"1"}
    # ESC 8 without a prior ESC 7 restores the baseline
    assert sgr(b"\x1b[1m", b"\x1b8") == RESET_SGR_CODES


def test_xterm_push_and_pop_sgr():
    # XTPUSHSGR: push bold
    expect = {**RESET_SGR_CODES, "weight": b"1", "fg": b"31"}
    assert sgr(b"\x1b[1m", b"\x1b[#{", b"\x1b[31m") == expect
    # XTPUSHSGR / XTPOPSGR: pop back to bold
    out = sgr(b"\x1b[1m", b"\x1b[#{", b"\x1b[31m", b"\x1b[#}")
    assert out == {**RESET_SGR_CODES, "weight": b"1"}


def test_pop_with_empty_stack_is_harmless():
    assert sgr(b"\x1b[1m", b"\x1b[#}") == {**RESET_SGR_CODES, "weight": b"1"}


def test_dec_private_mode_set_and_reset():
    assert dec(b"\x1b[?25h") == RESET_DEC_MODES  # matches baseline (visible)
    assert dec(b"\x1b[?25l") == {**RESET_DEC_MODES, 25: b"l"}  # hide cursor


def test_dec_mode_latest_action_wins():
    assert dec(b"\x1b[?25l", b"\x1b[?25h") == RESET_DEC_MODES
    assert dec(b"\x1b[?25h", b"\x1b[?25l") == {**RESET_DEC_MODES, 25: b"l"}


def test_dec_modes_combined_in_one_escape():
    # one DECSET carrying several ;-separated modes
    expect = {**RESET_DEC_MODES, 1: b"h", 25: b"h", 2004: b"h"}
    assert dec(b"\x1b[?1;25;2004h") == expect


def test_unknown_dec_mode_accumulates():
    # modes outside the baseline are still tracked
    assert dec(b"\x1b[?1234h") == {**RESET_DEC_MODES, 1234: b"h"}


def test_ansi_standard_modes():
    assert ansi(b"\x1b[4h") == {**RESET_ANSI_MODES, 4: b"h"}  # insert mode
    assert ansi(b"\x1b[20l") == RESET_ANSI_MODES  # newline off = baseline
    assert ansi(b"\x1b[4h", b"\x1b[20h") == {
        **RESET_ANSI_MODES,
        4: b"h",
        20: b"h",
    }


def test_skipped_dec_modes_not_captured():
    # 2 (DECANM), 3 (DECCOLM), 1048 (cursor save) lack restorable boolean state
    assert dec(b"\x1b[?2l") == RESET_DEC_MODES
    assert dec(b"\x1b[?3h") == RESET_DEC_MODES
    assert dec(b"\x1b[?1048h") == RESET_DEC_MODES
    # neighbours in the same escape are still captured
    assert dec(b"\x1b[?3;1049h") == {**RESET_DEC_MODES, 1049: b"h"}


def test_alternate_screen_mode_tracked():
    # full-screen apps toggle 1049; we track whichever state they left
    assert dec(b"\x1b[?1049h") == {**RESET_DEC_MODES, 1049: b"h"}
    assert dec(b"\x1b[?1049h", b"\x1b[?1049l") == RESET_DEC_MODES


def test_mouse_protocol_shared_register():
    # 9/1000/1001/1002/1003 share one register: the latest set wins and the
    # other members are recorded as reset, so values alone capture the state
    assert dec(b"\x1b[?1000h", b"\x1b[?1002h") == {
        **RESET_DEC_MODES,
        1002: b"h",
    }
    assert dec(b"\x1b[?1002h", b"\x1b[?1000h") == {
        **RESET_DEC_MODES,
        1000: b"h",
    }
    # resetting any member turns the whole register off (xterm semantics)
    assert dec(b"\x1b[?1003h", b"\x1b[?1000l") == RESET_DEC_MODES


def test_mouse_encoding_shared_register():
    # 1005/1006/1015/1016 select one coordinate encoding, likewise
    assert dec(b"\x1b[?1005h", b"\x1b[?1006h") == {
        **RESET_DEC_MODES,
        1006: b"h",
    }
    assert dec(b"\x1b[?1006h", b"\x1b[?1006l") == RESET_DEC_MODES
    # the two mouse families are independent of each other
    assert dec(b"\x1b[?1002h", b"\x1b[?1006h") == {
        **RESET_DEC_MODES,
        1002: b"h",
        1006: b"h",
    }


def test_mouse_protocol_replay_resets_before_setting():
    # replay must reset the old protocol before setting the new one, since
    # resetting any family member turns the register off again
    chunks = track(b"\x1b[?1000h", b"\x1b[?1002h").mode_chunks()
    assert chunks.index(b"\x1b[?1000l") < chunks.index(b"\x1b[?1002h")


def test_xtrestore_renormalizes_mouse_family():
    # restoring a saved protocol turns off whichever one is now active
    escapes = [b"\x1b[?1000h", b"\x1b[?1000s", b"\x1b[?1003h", b"\x1b[?1000r"]
    assert dec(*escapes) == {**RESET_DEC_MODES, 1000: b"h"}
    # restoring with nothing saved falls back to baseline = off for all
    assert dec(b"\x1b[?1003h", b"\x1b[?1000r") == RESET_DEC_MODES


def test_xtsave_and_xtrestore_dec_modes():
    # save the current value, change it, then restore the saved one
    assert track(b"\x1b[?1049h", b"\x1b[?1049s")._save_dec_xterm == {1049: b"h"}
    out = dec(b"\x1b[?1049h", b"\x1b[?1049s", b"\x1b[?1049l", b"\x1b[?1049r")
    assert out == {**RESET_DEC_MODES, 1049: b"h"}
    # restoring a mode that was never saved falls back to the baseline
    assert dec(b"\x1b[?1049h", b"\x1b[?1049r") == RESET_DEC_MODES
    # ...or forgets it entirely if the baseline doesn't cover it
    assert dec(b"\x1b[?1234h", b"\x1b[?1234r") == RESET_DEC_MODES


def test_decstr_soft_reset():
    # DECSTR restores SGR and the modes it governs (?25, 4) to baseline,
    # while non-governed modes (?2004) are kept
    escapes = [b"\x1b[1m", b"\x1b[?25l", b"\x1b[4h", b"\x1b[?2004h"]
    tracker = track(*escapes, b"\x1b[!p")
    assert tracker.sgr_codes == RESET_SGR_CODES
    assert tracker.dec_modes == {**RESET_DEC_MODES, 2004: b"h"}
    assert tracker.ansi_modes == RESET_ANSI_MODES


def test_state_after_decstr_kept():
    # a governed mode set again after the soft reset is tracked as usual
    out = dec(b"\x1b[?25l", b"\x1b[!p", b"\x1b[?25l")
    assert out == {**RESET_DEC_MODES, 25: b"l"}


def test_decstr_resets_dec_save_buffer():
    # DECSTR resets the DECSC buffer, so a later ESC 8 restores defaults
    assert sgr(b"\x1b[1m", b"\x1b7", b"\x1b[!p", b"\x1b8") == RESET_SGR_CODES


def test_ris_restores_baseline():
    # RIS returns everything (even non-DECSTR state) to the explicit baseline
    escapes = [b"\x1b[1m", b"\x1b[?25l", b"\x1b[4h", b"\x1b[3 q", b"\x1b(0"]
    tracker = track(*escapes, b"\x1bc")
    assert tracker.sgr_codes == RESET_SGR_CODES
    assert tracker.dec_modes == RESET_DEC_MODES
    assert tracker.ansi_modes == RESET_ANSI_MODES
    assert tracker.other_modes == RESET_OTHER_MODES
    # state accumulated after the reset is kept
    assert sgr(b"\x1b[1m", b"\x1bc", b"\x1b[31m") == {
        **RESET_SGR_CODES,
        "fg": b"31",
    }


def test_charset_designation_per_slot():
    # G0 = DEC special graphics
    assert other(b"\x1b(0") == {**RESET_OTHER_MODES, "G0": b"\x1b(0"}
    assert other(b"\x1b(0", b"\x1b(B") == RESET_OTHER_MODES  # latest G0 wins
    # G0 and G1 are independent slots
    expect = {**RESET_OTHER_MODES, "G0": b"\x1b(0", "G1": b"\x1b)0"}
    assert other(b"\x1b(0", b"\x1b)0") == expect
    # the 96-set designator (-) targets the same slot (G1) as ), latest wins
    assert other(b"\x1b)0", b"\x1b-A") == {**RESET_OTHER_MODES, "G1": b"\x1b-A"}


def test_charset_locking_shift():
    assert other(b"\x0e") == {**RESET_OTHER_MODES, "shift": b"\x0e"}  # SO: G1
    assert other(b"\x0e", b"\x0f") == RESET_OTHER_MODES  # latest (SI) wins
    assert other(b"\x1bn") == {**RESET_OTHER_MODES, "shift": b"\x1bn"}  # LS2


def test_keypad_mode():
    # application keypad (DECKPAM)
    assert other(b"\x1b=") == {**RESET_OTHER_MODES, "keypad": b"\x1b="}
    # numeric (DECKPNM) wins, matching the baseline
    assert other(b"\x1b=", b"\x1b>") == RESET_OTHER_MODES


def test_charset_and_keypad_reset_by_decstr_and_ris():
    # DECSTR and RIS both reset character sets and keypad to defaults
    assert other(b"\x1b(0", b"\x1b=", b"\x1b[!p") == RESET_OTHER_MODES
    assert other(b"\x1b(0", b"\x1b=", b"\x1bc") == RESET_OTHER_MODES


def test_char_protection_reset_by_decstr():
    # DECSCA (CSI Ps " q) is captured and IS reset by a soft reset
    assert other(b'\x1b[1"q') == {**RESET_OTHER_MODES, "decsca": b'\x1b[1"q'}
    assert other(b'\x1b[1"q', b"\x1b[!p") == RESET_OTHER_MODES


LEDS_OFF = {  # the decomposed all-off LED state
    "decll1": b"\x1b[21q",
    "decll2": b"\x1b[22q",
    "decll3": b"\x1b[23q",
}

OTHER_SANS_LED0 = {k: v for k, v in RESET_OTHER_MODES.items() if k != "decll0"}


def test_leds():
    # lighting an LED decomposes decll0 into explicit per-LED entries
    lit1 = {**OTHER_SANS_LED0, **LEDS_OFF, "decll1": b"\x1b[1q"}
    assert other(b"\x1b[1q") == lit1  # num lock on
    # an explicit off code for another LED doesn't change its (off) value
    assert other(b"\x1b[1q", b"\x1b[22q") == lit1
    # multiple lit LEDs coexist, each with its own entry
    lit12 = {**lit1, "decll2": b"\x1b[2q"}
    assert other(b"\x1b[1q", b"\x1b[2q") == lit12
    # LEDs survive a soft reset (only RIS clears them)
    assert other(b"\x1b[1q", b"\x1b[!p") == lit1
    assert other(b"\x1b[1q", b"\x1bc") == RESET_OTHER_MODES


def test_leds_collapse_to_all_off():
    # extinguishing the last lit LED collapses back to compact decll0
    assert other(b"\x1b[1q", b"\x1b[21q") == RESET_OTHER_MODES
    # CSI 0 q also collapses, superseding any still-lit LEDs
    assert other(b"\x1b[1q", b"\x1b[2q", b"\x1b[0q") == RESET_OTHER_MODES


def test_attribute_change_extent():
    expect = {**RESET_OTHER_MODES, "decsace": b"\x1b[2*x"}
    assert other(b"\x1b[2*x") == expect  # rectangle mode
    expect = {**RESET_OTHER_MODES, "decsace": b"\x1b[1*x"}
    assert other(b"\x1b[2*x", b"\x1b[1*x") == expect  # superceded
    # survives a soft reset, cleared only by RIS
    expect = {**RESET_OTHER_MODES, "decsace": b"\x1b[2*x"}
    assert other(b"\x1b[2*x", b"\x1b[!p") == expect
    assert other(b"\x1b[2*x", b"\x1bc") == RESET_OTHER_MODES


def test_cursor_style():
    expect = {**RESET_OTHER_MODES, "decscusr": b"\x1b[3 q"}
    assert other(b"\x1b[3 q") == expect  # blinking underline
    expect = {**RESET_OTHER_MODES, "decscusr": b"\x1b[1 q"}
    assert other(b"\x1b[3 q", b"\x1b[1 q") == expect  # superceded
    # cursor style survives a soft reset (vim/neovim rely on this), not RIS
    expect = {**RESET_OTHER_MODES, "decscusr": b"\x1b[3 q"}
    assert other(b"\x1b[3 q", b"\x1b[!p") == expect
    assert other(b"\x1b[3 q", b"\x1bc") == RESET_OTHER_MODES


def test_xterm_pointer_mode():
    # XTSMPOINTER (CSI > Ps p) selects when the mouse pointer auto-hides
    expect = {**RESET_OTHER_MODES, "xtsmpointer": b"\x1b[>2p"}
    assert other(b"\x1b[>2p") == expect
    expect = {**RESET_OTHER_MODES, "xtsmpointer": b"\x1b[>3p"}
    assert other(b"\x1b[>1p", b"\x1b[>3p") == expect  # superceded
    # survives a soft reset, cleared only by RIS
    expect = {**RESET_OTHER_MODES, "xtsmpointer": b"\x1b[>2p"}
    assert other(b"\x1b[>2p", b"\x1b[!p") == expect
    assert other(b"\x1b[>2p", b"\x1bc") == RESET_OTHER_MODES


#
# Serialization: how the state dicts are rendered by mode_chunks()
#

BASELINE_DEC_L_RUN = (
    b"\x1b[?1;6;9;47;66;1000;1001;1002;1003;1004;1005;1006;1015;1016;"
    b"1047;1049;2004;2026l"
)


def test_baseline_replay():
    # a fresh tracker replays DECSTR plus the explicit baseline
    assert TerminalModeTracker().mode_chunks() == [
        b"\x1b[m",  # SGR reset
        BASELINE_DEC_L_RUN,  # DEC mode resets, batched into one CSI
        b"\x1b[?7;25h",  # DEC mode sets, likewise
        b"\x1b[2;4;20l",  # ANSI mode resets
        b"\x1b[0q",  # keyboard LEDs off
        b"\x1b[0*x",  # stream mode for rectangle operations
        b'\x1b[0"q',  # character protection off
        b"\x1b[0 q",  # default cursor style
        b"\x1b(B",  # G0 = US-ASCII
        b"\x1b)B",  # G1 = US-ASCII
        b"\x1b>",  # numeric keypad
        b"\x0f",  # SI: GL = G0
        b"\x1b[>1p",  # default pointer hiding
    ]


def test_serialization_of_dicts():
    tracker = TerminalModeTracker()
    tracker.sgr_codes = {"RESET": b"", "weight": b"1"}
    tracker.dec_modes = {1000: b"h", 1006: b"h", 7: b"l", 25: b"h"}
    tracker.ansi_modes = {4: b"h", 20: b"h"}
    tracker.other_modes = {"G0": b"\x1b(0", "keypad": b"\x1b="}
    assert tracker.mode_chunks() == [
        b"\x1b[;1m",  # SGR values joined into one escape
        b"\x1b[?1000;1006h",  # consecutive same-action DEC modes batch...
        b"\x1b[?7l",  # ...runs break when the action changes...
        b"\x1b[?25h",  # ...and don't rejoin the earlier run
        b"\x1b[4;20h",  # ANSI modes batch separately from DEC modes
        b"\x1b(0",  # other modes replay verbatim in dict order
        b"\x1b=",
    ]


def test_empty_dicts_serialize_to_nothing():
    tracker = TerminalModeTracker()
    tracker.sgr_codes = {}
    tracker.dec_modes = {}
    tracker.ansi_modes = {}
    tracker.other_modes = {}
    assert tracker.mode_chunks() == []


#
# Diffs: mode_chunks(base) emits only the transition from the base state
#


def test_diff_between_fresh_trackers_is_empty():
    assert TerminalModeTracker().mode_chunks(TerminalModeTracker()) == []


def test_diff_against_self_is_empty():
    tracker = track(b"\x1b[1;31m", b"\x1b[?25l", b"\x1b[4h", b"\x1b(0")
    assert tracker.mode_chunks(tracker) == []


def test_diff_emits_only_changed_dec_modes():
    base = track(b"\x1b[?25l")
    target = track(b"\x1b[?25l", b"\x1b[?2004h")
    assert target.mode_chunks(base) == [b"\x1b[?2004h"]


def test_diff_returns_changed_dec_mode_to_baseline():
    # the target never touched ?25, so the diff undoes the base's change
    base = track(b"\x1b[?25l")
    assert TerminalModeTracker().mode_chunks(base) == [b"\x1b[?25h"]


def test_diff_unknown_dec_mode():
    # a mode outside the baseline still diffs by value
    target = track(b"\x1b[?1234h")
    assert target.mode_chunks(TerminalModeTracker()) == [b"\x1b[?1234h"]


def test_diff_batches_changed_modes_across_unchanged_ones():
    # 2004 matches the base and is skipped; 1049 and 69 join one h run anyway
    base = track(b"\x1b[?2004h")
    target = track(b"\x1b[?1049h", b"\x1b[?2004h", b"\x1b[?69h")
    assert target.mode_chunks(base) == [b"\x1b[?1049;69h"]


def test_diff_reemits_full_sgr_on_any_change():
    # SGR isn't diffed code-by-code; any difference replays the whole state
    base = track(b"\x1b[1m")
    target = track(b"\x1b[1m", b"\x1b[31m")
    assert target.mode_chunks(base) == [b"\x1b[;1;31m"]


def test_diff_sgr_compares_values_not_order():
    # categories are independent, so equal values in a different order match
    base = track(b"\x1b[1m", b"\x1b[31m")
    target = track(b"\x1b[31m", b"\x1b[1m")
    assert target.mode_chunks(base) == []


def test_diff_ansi_modes():
    base = track(b"\x1b[4h")
    target = track(b"\x1b[4h", b"\x1b[20h")
    assert target.mode_chunks(base) == [b"\x1b[20h"]


def test_diff_other_modes():
    # changed entries replay; the base's cursor style is undone to the default
    base = track(b"\x1b(0", b"\x1b[3 q")
    target = track(b"\x1b(0", b"\x1b=")
    assert target.mode_chunks(base) == [b"\x1b[0 q", b"\x1b="]


def test_diff_mouse_protocol_switch():
    # same values, different winner can no longer happen (normalization);
    # the diff resets the base's protocol, then sets the target's
    base = track(b"\x1b[?1000h", b"\x1b[?1002h")  # button-motion tracking
    target = track(b"\x1b[?1002h", b"\x1b[?1000h")  # click-only tracking
    assert target.mode_chunks(base) == [b"\x1b[?1002l", b"\x1b[?1000h"]


def test_diff_leds():
    lit = track(b"\x1b[1q")
    dark = TerminalModeTracker()
    # dark -> lit: the decomposed entries all replay (the base lacks them)
    assert lit.mode_chunks(dark) == [b"\x1b[22q", b"\x1b[23q", b"\x1b[1q"]
    # lit -> dark: decll0 has no counterpart in the base, so all-off replays
    assert dark.mode_chunks(lit) == [b"\x1b[0q"]


def test_diff_assumes_unknown_base_modes_reset():
    # modes only the base tracked are assumed to default to reset...
    base = track(b"\x1b[?1234h", b"\x1b[?5678h")
    assert TerminalModeTracker().mode_chunks(base) == [b"\x1b[?1234;5678l"]
    # ...so a base-only mode already reset needs no transition at all
    assert TerminalModeTracker().mode_chunks(track(b"\x1b[?4321l")) == []
    # ANSI modes get the same treatment
    base = track(b"\x1b[34h")
    assert TerminalModeTracker().mode_chunks(base) == [b"\x1b[34l"]


def test_diff_base_only_modes_join_runs():
    base = track(b"\x1b[?1234h")
    target = track(b"\x1b[?7l")  # auto-wrap off
    assert target.mode_chunks(base) == [b"\x1b[?7;1234l"]


def test_diff_undoes_base_only_charset_designations():
    # G2/G3 aren't in the baseline, but have a known default to return to
    base = track(b"\x1b*0", b"\x1b+0")  # G2/G3 = DEC special graphics
    assert TerminalModeTracker().mode_chunks(base) == [b"\x1b*B", b"\x1b+B"]


def test_diff_combined_stores():
    base = track(b"\x1b[?1049h", b"\x1b[?25l")
    target = track(b"\x1b[1m", b"\x1b[?1049h", b"\x1b[4h")
    assert target.mode_chunks(base) == [
        b"\x1b[;1m",  # SGR re-emitted in full
        b"\x1b[?25h",  # base's hidden cursor undone; shared 1049 untouched
        b"\x1b[4h",  # insert mode set
    ]


def test_end_to_end_replay():
    # tracked changes replace their baseline entries and replay after them
    tracker = track(b"\x1b[1m", b"\x1b[?25l", b"\x1b[?1000h", b"\x1b[4h")
    assert tracker.mode_chunks() == [
        b"\x1b[;1m",  # reset, then bold
        b"\x1b[?1;6;9;47;66;1001;1002;1003;1004;1005;1006;1015;1016;"
        b"1047;1049;2004;2026l",  # baseline resets, minus the changed 1000
        b"\x1b[?7h",  # baseline, no longer joined by 25
        b"\x1b[?25l",  # changed modes moved to the end, in change order
        b"\x1b[?1000h",
        b"\x1b[2;20l",  # ANSI baseline, minus the changed 4
        b"\x1b[4h",
        b"\x1b[0q",
        b"\x1b[0*x",
        b'\x1b[0"q',
        b"\x1b[0 q",
        b"\x1b(B",
        b"\x1b)B",
        b"\x1b>",
        b"\x0f",
        b"\x1b[>1p",
    ]
