"""Unit tests for ok_serial._terminal_mode_saver."""

from ok_serial._terminal_mode_saver import TerminalModeSaver


def restore(*escapes: bytes) -> bytes:
    """Feeds escapes to a fresh saver and returns its restore sequence."""
    saver = TerminalModeSaver()
    for escape in escapes:
        saver.add_escape(escape)
    return saver.get_escape()


def test_empty_state_resets():
    # nothing accumulated -> a bare reset (leading ; is an implicit "0")
    assert restore() == b"\x1b[;m"


def test_single_attribute_kept():
    assert restore(b"\x1b[1m") == b"\x1b[;1m"  # bold
    assert restore(b"\x1b[31m") == b"\x1b[;31m"  # red foreground


def test_combined_params_in_one_escape():
    # one CSI carrying several ;-separated codes
    assert restore(b"\x1b[1;31m") == b"\x1b[;1;31m"


def test_latest_in_category_wins():
    # intensity is one category: 22 (normal) supersedes 1 (bold)
    assert restore(b"\x1b[1m", b"\x1b[22m") == b"\x1b[;22m"
    # foreground is one category: blue supersedes red
    assert restore(b"\x1b[31m", b"\x1b[34m") == b"\x1b[;34m"


def test_independent_categories_coexist():
    out = restore(b"\x1b[1m", b"\x1b[31m", b"\x1b[42m")
    assert out == b"\x1b[;1;31;42m"


def test_reset_clears_everything():
    assert restore(b"\x1b[1;31m", b"\x1b[0m") == b"\x1b[;m"
    # empty parameter defaults to 0 == reset (ECMA-48), so ESC[m clears too
    assert restore(b"\x1b[1;31m", b"\x1b[m") == b"\x1b[;m"
    # an empty param mid-sequence resets in place
    assert restore(b"\x1b[31;;1m") == b"\x1b[;1m"


def test_256_color_and_truecolor():
    assert restore(b"\x1b[38;5;200m") == b"\x1b[;38;5;200m"  # indexed fg
    assert restore(b"\x1b[48;2;10;20;30m") == b"\x1b[;48;2;10;20;30m"  # rgb bg


def test_overline_frame_and_other_cancel_groups():
    # each "off" code supersedes its "on" code instead of accumulating
    assert restore(b"\x1b[53m", b"\x1b[55m") == b"\x1b[;55m"  # overline off
    assert restore(b"\x1b[51m", b"\x1b[52m") == b"\x1b[;52m"  # frame -> circle
    assert restore(b"\x1b[52m", b"\x1b[54m") == b"\x1b[;54m"  # frame off
    assert restore(b"\x1b[11m", b"\x1b[13m") == b"\x1b[;13m"  # alt font latest
    assert restore(b"\x1b[26m", b"\x1b[50m") == b"\x1b[;50m"  # prop spacing off
    assert restore(b"\x1b[60m", b"\x1b[65m") == b"\x1b[;65m"  # ideogram off
    assert restore(b"\x1b[73m", b"\x1b[75m") == b"\x1b[;75m"  # superscript off
    # 23 cancels both italic (3) and Fraktur (20): same category
    assert restore(b"\x1b[20m", b"\x1b[23m") == b"\x1b[;23m"


def test_extension_color_and_styled_underline():
    # styled underline (kitty 4:3 = curly) is one category with 4/21/24
    assert restore(b"\x1b[4:3m", b"\x1b[24m") == b"\x1b[;24m"
    # underline color (T.416) is its own color category
    assert restore(b"\x1b[58;5;9m") == b"\x1b[;58;5;9m"
    assert restore(b"\x1b[58;2;1;2;3m", b"\x1b[59m") == b"\x1b[;59m"


def test_new_groups_do_not_shadow_colors():
    # codes that share a leading digit with the new groups still route right
    assert restore(b"\x1b[100m") == b"\x1b[;100m"  # bright bg, not font 10
    assert restore(b"\x1b[44m") == b"\x1b[;44m"  # blue bg, not styled underline
    assert restore(b"\x1b[5m") == b"\x1b[;5m"  # blink, not frame/overline


def test_unknown_multidigit_code_accumulates():
    # genuinely unknown codes (56/57 are reserved) still accumulate by value
    assert restore(b"\x1b[56m", b"\x1b[57m") == b"\x1b[;56;57m"


def test_8bit_csi_introducer():
    # 0x9b is the single-byte CSI, equivalent to ESC [
    assert restore(b"\x9b1m") == b"\x1b[;1m"


def test_non_style_escapes_ignored():
    # cursor move and erase carry no style; state stays empty
    assert restore(b"\x1b[2J", b"\x1b[H", b"\x1b[10;5H") == b"\x1b[;m"
    # ...and they don't disturb an existing style
    assert restore(b"\x1b[1m", b"\x1b[2J") == b"\x1b[;1m"


def test_dec_save_and_restore():
    # ESC 7 snapshots the style, ESC 8 restores it
    saver = TerminalModeSaver()
    saver.add_escape(b"\x1b[1m")
    saver.add_escape(b"\x1b7")  # DECSC: save bold
    saver.add_escape(b"\x1b[31m")  # add red on top
    assert saver.get_escape() == b"\x1b[;1;31m"
    saver.add_escape(b"\x1b8")  # DECRC: back to just bold
    assert saver.get_escape() == b"\x1b[;1m"


def test_xterm_push_and_pop_sgr():
    saver = TerminalModeSaver()
    saver.add_escape(b"\x1b[1m")
    saver.add_escape(b"\x1b[#{")  # XTPUSHSGR: push bold
    saver.add_escape(b"\x1b[31m")
    assert saver.get_escape() == b"\x1b[;1;31m"
    saver.add_escape(b"\x1b[#}")  # XTPOPSGR: pop back to bold
    assert saver.get_escape() == b"\x1b[;1m"


def test_pop_with_empty_stack_is_harmless():
    assert restore(b"\x1b[1m", b"\x1b[#}") == b"\x1b[;1m"
