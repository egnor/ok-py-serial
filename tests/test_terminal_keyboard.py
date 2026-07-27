from ok_serial.terminal.keyboard import (
    TerminalKeyEvent,
    chunk_to_key_event as key,
    _KEY_CODES,
)


def key_text(chunk: bytes | str) -> str | None:
    return k.text if (k := key(chunk)) else None


def key_desc(chunk: bytes | str) -> str | None:
    if not (k := key(chunk)):
        return None
    mods = [m for m in "shift alt ctrl".split() if getattr(k, m)]
    return "-".join([*mods, k.name])


def test_plain_control_bytes():
    assert key(b"\x1c") == TerminalKeyEvent(ord("\\"), text="\x1c", ctrl=True)
    assert key(b"\x1d") == TerminalKeyEvent(ord("]"), text="\x1d", ctrl=True)
    assert key(b"\x03") == TerminalKeyEvent(ord("c"), text="\x03", ctrl=True)


def test_kitty_key_reports():
    # kitty keyboard protocol: CSI unicode-key;mods u, ctrl = bit 4 in mods-1
    expect = TerminalKeyEvent(ord("\\"), text="\x1c", ctrl=True)
    assert key(b"\x1b[92;5u") == expect
    assert key_text(b"\x1b[93;5u") == "\x1d"  # ctrl-]
    assert key_text(b"\x1b[99;5u") == "\x03"  # ctrl-c
    assert key_text(b"\x9b92;5u") == "\x1c"  # 8-bit CSI

    # extra modifiers alongside ctrl still count
    assert key_text(b"\x1b[92;13u") == "\x1c"  # ctrl+super

    # alternate-key subparameters on the key field are ignored
    assert key_text(b"\x1b[92:124;5u") == "\x1c"

    # explicit text-as-codepoints is decoded and takes precedence
    expect = TerminalKeyEvent(ord("a"), text="A", shift=True)
    assert key(b"\x1b[97;2;65u") == expect


def test_kitty_event_types():
    # press (1, or omitted) and repeat (2) count; release (3) does not
    assert key_text(b"\x1b[92;5:1u") == "\x1c"
    assert key_text(b"\x1b[92;5:2u") == "\x1c"
    assert key(b"\x1b[92;5:3u") is None


def test_modify_other_keys_reports():
    # xterm modifyOtherKeys: CSI 27;mods;key ~
    assert key_text(b"\x1b[27;5;92~") == "\x1c"
    assert key_text(b"\x1b[27;5;93~") == "\x1d"

    # some terminals report the resulting control code instead of the key
    expect = TerminalKeyEvent(ord("\\"), text="\x1c", ctrl=True)
    assert key(b"\x1b[27;5;28~") == expect


def test_modifier_decoding():
    assert key(b"\x1b[92;1u") == TerminalKeyEvent(92)
    assert key(b"\x1b[92u") == TerminalKeyEvent(92)
    assert key(b"\x1b[92;2u") == TerminalKeyEvent(92, shift=True)  # no text
    assert key(b"\x1b[92;3u") == TerminalKeyEvent(92, alt=True)


def test_key_codes():
    # spot check codepoints against the kitty spec's table
    assert _KEY_CODES["ESCAPE"] == 57344
    assert _KEY_CODES["CAPS_LOCK"] == 57358
    assert _KEY_CODES["F13"] == 57376
    assert _KEY_CODES["KP_0"] == 57399
    assert _KEY_CODES["ISO_LEVEL5_SHIFT"] == 57454


def test_classic_cursor_and_edit_keys():
    assert key_desc(b"\x1b[A") == "UP"
    assert key_desc(b"\x9bB") == "DOWN"  # 8-bit CSI
    assert key_desc(b"\x1bOC") == "RIGHT"  # app cursor mode
    assert key_desc(b"\x8fC") == "RIGHT"  # 8-bit SS3
    assert key_desc(b"\x1b[1;5D") == "ctrl-LEFT"
    assert key_desc(b"\x1b[H") == "HOME"
    assert key_desc(b"\x1b[1~") == "HOME"
    assert key_desc(b"\x1b[7~") == "HOME"
    assert key_desc(b"\x1b[1;2F") == "shift-END"
    assert key_desc(b"\x1b[3~") == "DELETE"
    assert key_desc(b"\x1b[2~") == "INSERT"
    assert key_desc(b"\x1b[5;3~") == "alt-PAGE_UP"
    assert key_desc(b"\x1b[6~") == "PAGE_DOWN"


def test_classic_function_keys():
    assert key_desc(b"\x1bOP") == "F1"
    assert key_desc(b"\x1b[1;2P") == "shift-F1"
    assert key_desc(b"\x1b[11~") == "F1"
    assert key_desc(b"\x1bOR") == "F3"
    assert key_desc(b"\x1b[13~") == "F3"
    assert key_desc(b"\x1b[15~") == "F5"
    assert key_desc(b"\x1b[24;6~") == "shift-ctrl-F12"
    assert key_desc(b"\x1b[34~") == "F20"


def test_classic_keypad_keys():
    assert key_desc(b"\x1bOM") == "KP_ENTER"
    assert key_desc(b"\x1bOp") == "KP_0"
    assert key_desc(b"\x1bOy") == "KP_9"
    assert key_desc(b"\x1bOo") == "KP_DIVIDE"
    assert key_desc(b"\x1b[E") == "KP_BEGIN"
    assert key_desc(b"\x1b[57427~") == "KP_BEGIN"


def test_backtab_and_delete_byte():
    # backtab is the tab key (reported as ctrl-i style, like CSI 9;2u)
    assert key_desc(b"\x1b[Z") == "shift-i"
    assert key_desc(b"\x7f") == "\x7f"


def test_classic_event_types():
    # kitty event subparams also apply to legacy-encoded functional keys
    assert key_desc(b"\x1b[1;1:2A") == "UP"  # repeat
    assert key(b"\x1b[1;1:3A") is None  # release
    assert key(b"\x1b[5;1:3~") is None  # release


def test_kitty_functional_key_reports():
    assert key_desc(b"\x1b[57376u") == "F13"
    assert key_desc(b"\x1b[57413;5u") == "ctrl-KP_ADD"


def test_non_key_chunks_ignored():
    assert key("hello") is None  # text chunks
    assert key(b"\x1b[3;7R") is None  # cursor position reply
    assert key(b"\x1b[1;5R") is None  # CPR again, NOT modified F3
    assert key(b"\x1b[<35;1;2M") is None  # mouse report
    assert key(b"\x1b[?1u") is None  # kitty flags query reply
    assert key(b"\x1b[200~") is None  # bracketed paste marker
    assert key(b"\x1b[G") is None  # not a known key letter
    assert key(b"\x1b[0c") is None  # device attributes reply
