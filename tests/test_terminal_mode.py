"""Unit tests for ok_serial._terminal_mode."""

from ok_serial._terminal_mode import TerminalMode


def restore(*escapes: bytes) -> list[bytes]:
    """Feeds escapes to a fresh mode and returns its restore sequence."""
    mode = TerminalMode()
    for escape in escapes:
        mode.add_escape(escape)
    return mode.replay_escapes()


def test_empty_state():
    # nothing accumulated -> nothing restored
    assert restore() == []


def test_single_attribute_kept():
    assert restore(b"\x1b[1m") == [b"\x1b[1m"]  # bold
    assert restore(b"\x1b[31m") == [b"\x1b[31m"]  # red foreground


def test_combined_params_in_one_escape():
    # one CSI carrying several ;-separated codes
    assert restore(b"\x1b[1;31m") == [b"\x1b[1;31m"]


def test_latest_in_category_wins():
    # intensity is one category: 22 (normal) supersedes 1 (bold)
    assert restore(b"\x1b[1m", b"\x1b[22m") == [b"\x1b[22m"]
    # foreground is one category: blue supersedes red
    assert restore(b"\x1b[31m", b"\x1b[34m") == [b"\x1b[34m"]


def test_independent_categories_coexist():
    out = restore(b"\x1b[1m", b"\x1b[31m", b"\x1b[42m")
    assert out == [b"\x1b[1;31;42m"]


def test_reset_clears_everything():
    assert restore(b"\x1b[1;31m", b"\x1b[0m") == [b"\x1b[0m"]
    # empty parameter defaults to 0 == reset (ECMA-48), so ESC[m clears too
    assert restore(b"\x1b[1;31m", b"\x1b[m") == [b"\x1b[m"]
    # an empty param mid-sequence resets in place
    assert restore(b"\x1b[31;;1m") == [b"\x1b[;1m"]


def test_256_color_and_truecolor():
    assert restore(b"\x1b[38;5;200m") == [b"\x1b[38;5;200m"]  # indexed fg
    assert restore(b"\x1b[48;2;10;20;30m") == [b"\x1b[48;2;10;20;30m"]  # rgb bg


def test_overline_frame_and_other_cancel_groups():
    # each "off" code supersedes its "on" code instead of accumulating
    assert restore(b"\x1b[53m", b"\x1b[55m") == [b"\x1b[55m"]  # overline off
    assert restore(b"\x1b[51m", b"\x1b[52m") == [b"\x1b[52m"]  # frame -> circle
    assert restore(b"\x1b[52m", b"\x1b[54m") == [b"\x1b[54m"]  # frame off
    assert restore(b"\x1b[11m", b"\x1b[13m") == [b"\x1b[13m"]  # alt font latest
    assert restore(b"\x1b[26m", b"\x1b[50m") == [b"\x1b[50m"]  # no prop spacing
    assert restore(b"\x1b[60m", b"\x1b[65m") == [b"\x1b[65m"]  # ideogram off
    assert restore(b"\x1b[73m", b"\x1b[75m") == [b"\x1b[75m"]  # superscript off
    # 23 cancels both italic (3) and Fraktur (20): same category
    assert restore(b"\x1b[20m", b"\x1b[23m") == [b"\x1b[23m"]


def test_extension_color_and_styled_underline():
    # styled underline (kitty 4:3 = curly) is one category with 4/21/24
    assert restore(b"\x1b[4:3m", b"\x1b[24m") == [b"\x1b[24m"]
    # underline color (T.416) is its own color category
    assert restore(b"\x1b[58;5;9m") == [b"\x1b[58;5;9m"]
    assert restore(b"\x1b[58;2;1;2;3m", b"\x1b[59m") == [b"\x1b[59m"]


def test_new_groups_do_not_shadow_colors():
    # codes that share a leading digit with the new groups still route right
    assert restore(b"\x1b[100m") == [b"\x1b[100m"]  # bright bg, not font 10
    assert restore(b"\x1b[44m") == [b"\x1b[44m"]  # blue bg, not underline
    assert restore(b"\x1b[5m") == [b"\x1b[5m"]  # blink, not frame/overline


def test_unknown_multidigit_code_accumulates():
    # genuinely unknown codes (56/57 are reserved) still accumulate by value
    assert restore(b"\x1b[56m", b"\x1b[57m") == [b"\x1b[56;57m"]


def test_8bit_csi_introducer():
    # 0x9b is the single-byte CSI, equivalent to ESC [
    assert restore(b"\x9b1m") == [b"\x1b[1m"]


def test_non_style_escapes_ignored():
    # cursor move and erase carry no style; state stays empty
    assert restore(b"\x1b[2J", b"\x1b[H", b"\x1b[10;5H") == []
    # ...and they don't disturb an existing style
    assert restore(b"\x1b[1m", b"\x1b[2J") == [b"\x1b[1m"]


def test_dec_save_and_restore():
    # ESC 7 snapshots the style, ESC 8 restores it
    mode = TerminalMode()
    mode.add_escape(b"\x1b[1m")
    mode.add_escape(b"\x1b7")  # DECSC: save bold
    mode.add_escape(b"\x1b[31m")  # add red on top
    assert mode.replay_escapes() == [b"\x1b[1;31m"]
    mode.add_escape(b"\x1b8")  # DECRC: back to just bold
    assert mode.replay_escapes() == [b"\x1b[1m"]


def test_xterm_push_and_pop_sgr():
    mode = TerminalMode()
    mode.add_escape(b"\x1b[1m")
    mode.add_escape(b"\x1b[#{")  # XTPUSHSGR: push bold
    mode.add_escape(b"\x1b[31m")
    assert mode.replay_escapes() == [b"\x1b[1;31m"]
    mode.add_escape(b"\x1b[#}")  # XTPOPSGR: pop back to bold
    assert mode.replay_escapes() == [b"\x1b[1m"]


def test_pop_with_empty_stack_is_harmless():
    assert restore(b"\x1b[1m", b"\x1b[#}") == [b"\x1b[1m"]


def test_dec_private_mode_set_and_reset():
    assert restore(b"\x1b[?25h") == [b"\x1b[?25h"]  # show cursor
    assert restore(b"\x1b[?25l") == [b"\x1b[?25l"]  # hide cursor


def test_dec_mode_latest_action_wins():
    assert restore(b"\x1b[?25l", b"\x1b[?25h") == [b"\x1b[?25h"]
    assert restore(b"\x1b[?25h", b"\x1b[?25l") == [b"\x1b[?25l"]


def test_dec_modes_grouped_by_action():
    # several modes, some set some reset, collapse into one CSI per action
    out = restore(b"\x1b[?1h", b"\x1b[?25h", b"\x1b[?7l")
    assert out == [b"\x1b[?1;25h", b"\x1b[?7l"]


def test_dec_modes_combined_in_one_escape():
    # one DECSET carrying several ;-separated modes
    assert restore(b"\x1b[?1;25;2004h") == [b"\x1b[?1;25;2004h"]


def test_ansi_standard_modes():
    assert restore(b"\x1b[4h") == [b"\x1b[4h"]  # insert mode on
    assert restore(b"\x1b[20l") == [b"\x1b[20l"]  # newline mode off
    assert restore(b"\x1b[4h", b"\x1b[20h") == [b"\x1b[4;20h"]


def test_sgr_and_modes_coexist():
    out = restore(b"\x1b[1m", b"\x1b[?25l", b"\x1b[4h")
    assert out == [b"\x1b[1m", b"\x1b[?25l", b"\x1b[4h"]


def test_skipped_dec_modes_not_captured():
    # 2 (DECANM), 3 (DECCOLM), 1048 (cursor save) lack restorable boolean state
    assert restore(b"\x1b[?2l") == []
    assert restore(b"\x1b[?3h") == []
    assert restore(b"\x1b[?1048h") == []
    # neighbours in the same escape are still captured
    assert restore(b"\x1b[?3;25h") == [b"\x1b[?25h"]


def test_modes_via_8bit_csi_introducer():
    assert restore(b"\x9b?25l") == [b"\x1b[?25l"]
    assert restore(b"\x9b4h") == [b"\x1b[4h"]


def test_alternate_screen_mode_restored():
    # full-screen apps toggle 1049; we restore whichever state they left
    assert restore(b"\x1b[?1049h") == [b"\x1b[?1049h"]
    assert restore(b"\x1b[?1049h", b"\x1b[?1049l") == [b"\x1b[?1049l"]


def test_mouse_protocol_modes_replay_in_order():
    # 1000/1002/1003 share one register in the terminal; we don't track that,
    # but replaying in set order lands on the right final protocol (latest set)
    assert restore(b"\x1b[?1002h", b"\x1b[?1000h") == [b"\x1b[?1002;1000h"]
    assert restore(b"\x1b[?1000h", b"\x1b[?1002h") == [b"\x1b[?1000;1002h"]
    # a reset breaks the run, so it replays after the set that precedes it
    out = restore(b"\x1b[?1000h", b"\x1b[?1003l")
    assert out == [b"\x1b[?1000h", b"\x1b[?1003l"]


def test_mode_runs_follow_set_order_not_action():
    # set / reset / set replays as three CSIs, preserving global order
    out = restore(b"\x1b[?1h", b"\x1b[?7l", b"\x1b[?25h")
    assert out == [b"\x1b[?1h", b"\x1b[?7l", b"\x1b[?25h"]


def test_independent_modes_batch_into_one_csi():
    # adjacent modes with the same action still collapse into a single CSI
    assert restore(b"\x1b[?1000h", b"\x1b[?1006h") == [b"\x1b[?1000;1006h"]
    assert restore(b"\x1b[?47h", b"\x1b[?1049h") == [b"\x1b[?47;1049h"]


def test_xtsave_and_xtrestore_dec_modes():
    # save the current value, change it, then restore the saved one
    out = restore(b"\x1b[?25h", b"\x1b[?25s", b"\x1b[?25l", b"\x1b[?25r")
    assert out == [b"\x1b[?25h"]
    # restoring a mode that was never saved forgets it (falls back to baseline)
    assert restore(b"\x1b[?25h", b"\x1b[?25r") == []


def test_decstr_soft_reset_is_replayed():
    # DECSTR is replayed verbatim; it resets SGR and the modes it governs (25,
    # 4), while non-governed modes (2004) and later deltas are kept after it
    input = [b"\x1b[1m", b"\x1b[?25l", b"\x1b[4h", b"\x1b[?2004h", b"\x1b[!p"]
    assert restore(*input) == [b"\x1b[!p", b"\x1b[?2004h"]


def test_state_after_decstr_replays_after_it():
    # a governed mode set again after the reset reappears, following the DECSTR
    input = [b"\x1b[?25l", b"\x1b[!p", b"\x1b[?25l"]
    assert restore(*input) == [b"\x1b[!p", b"\x1b[?25l"]


def test_ris_replayed_as_soft_reset():
    # RIS would clear the screen, so we downgrade it to a DECSTR on restore
    out = restore(b"\x1b[1m", b"\x1b[?25l", b"\x1b[4h", b"\x1bc")
    assert out == [b"\x1b[!p"]
    # state accumulated after the reset is kept, following the DECSTR
    out = restore(b"\x1b[1m", b"\x1bc", b"\x1b[31m")
    assert out == [b"\x1b[!p", b"\x1b[31m"]


def test_dec_and_ansi_modes_do_not_merge():
    # DEC ?...h and ANSI ...h must stay in separate CSIs (different meaning!)
    assert restore(b"\x1b[?1000h", b"\x1b[4h") == [b"\x1b[?1000h", b"\x1b[4h"]


def test_charset_designation_per_slot():
    assert restore(b"\x1b(0") == [b"\x1b(0"]  # G0 = DEC special graphics
    assert restore(b"\x1b(0", b"\x1b(B") == [b"\x1b(B"]  # latest G0 wins
    # G0 and G1 are independent slots and both restore
    assert restore(b"\x1b(0", b"\x1b)B") == [b"\x1b(0", b"\x1b)B"]
    # the 96-set designator (-) targets the same slot (G1) as ), latest wins
    assert restore(b"\x1b)B", b"\x1b-A") == [b"\x1b-A"]


def test_charset_locking_shift():
    assert restore(b"\x0e") == [b"\x0e"]  # SO -> GL = G1
    assert restore(b"\x0e", b"\x0f") == [b"\x0f"]  # latest shift wins (SI)
    assert restore(b"\x1bn") == [b"\x1bn"]  # LS2 -> GL = G2
    # designation plus shift: declare G1 line-drawing, then shift to it
    assert restore(b"\x1b)0", b"\x0e") == [b"\x1b)0", b"\x0e"]


def test_keypad_mode():
    assert restore(b"\x1b=") == [b"\x1b="]  # application keypad (DECKPAM)
    assert restore(b"\x1b=", b"\x1b>") == [b"\x1b>"]  # numeric wins (DECKPNM)


def test_charset_and_keypad_ordering():
    out = restore(b"\x1b[1m", b"\x1b[?25l", b"\x1b(0", b"\x1b=")
    assert out == [b"\x1b[1m", b"\x1b[?25l", b"\x1b(0", b"\x1b="]


def test_charset_and_keypad_reset_by_decstr_and_ris():
    # DECSTR and RIS both reset character sets and keypad to defaults
    assert restore(b"\x1b(0", b"\x1b=", b"\x1b[!p") == [b"\x1b[!p"]
    assert restore(b"\x1b(0", b"\x1b=", b"\x1bc") == [b"\x1b[!p"]


def test_char_protection_reset_by_decstr():
    # DECSCA (CSI Ps " q) is captured and, unlike cursor style, IS reset by a
    # soft reset, so we drop it and let the replayed DECSTR re-establish default
    assert restore(b'\x1b[1"q') == [b'\x1b[1"q']  # protect characters
    assert restore(b'\x1b[1"q', b"\x1b[!p") == [b"\x1b[!p"]


def test_leds():
    assert restore(b"\x1b[1q") == [b"\x1b[1q"]  # num lock on
    out = restore(b"\x1b[1q", b"\x1b[22q")
    assert out == [b"\x1b[1q", b"\x1b[22q"]  # +num, -caps
    out = restore(b"\x1b[1q", b"\x1b[22q", b"\x1b[21q")
    assert out == [b"\x1b[22q", b"\x1b[21q"]
    # LEDs survive a soft reset (only RIS clears them), so replay after DECSTR
    assert restore(b"\x1b[1q", b"\x1b[!p") == [b"\x1b[!p", b"\x1b[1q"]
    assert restore(b"\x1b[1q", b"\x1bc") == [b"\x1b[!p"]  # RIS clears LEDs


def test_leds_zero_clears_all():
    # CSI 0 q turns every LED off, superseding any still-lit ones
    assert restore(b"\x1b[1q", b"\x1b[2q", b"\x1b[0q") == [b"\x1b[0q"]


def test_cursor_style():
    assert restore(b"\x1b[3 q") == [b"\x1b[3 q"]  # blinking underline
    assert restore(b"\x1b[3 q", b"\x1b[1 q") == [b"\x1b[1 q"]  # superceded
    # cursor style survives a soft reset (vim/neovim rely on this), not RIS
    assert restore(b"\x1b[3 q", b"\x1b[!p") == [b"\x1b[!p", b"\x1b[3 q"]
    assert restore(b"\x1b[3 q", b"\x1bc") == [b"\x1b[!p"]  # RIS clears it


def test_xterm_pointer_mode():
    # XTSMPOINTER (CSI > Ps p) selects when the mouse pointer auto-hides
    assert restore(b"\x1b[>2p") == [b"\x1b[>2p"]
    assert restore(b"\x1b[>1p", b"\x1b[>3p") == [b"\x1b[>3p"]  # latest wins
    # survives a soft reset, cleared only by RIS
    assert restore(b"\x1b[>2p", b"\x1b[!p") == [b"\x1b[!p", b"\x1b[>2p"]
    assert restore(b"\x1b[>2p", b"\x1bc") == [b"\x1b[!p"]
