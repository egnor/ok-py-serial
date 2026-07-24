from ok_serial.terminal.keyboard import (
    TerminalKeyEvent,
    chunk_to_key_event as key,
)


def test_plain_control_bytes():
    assert key(b"\x1c") == TerminalKeyEvent(ord("\\"), text="\x1c", ctrl=True)
    assert key(b"\x1d") == TerminalKeyEvent(ord("]"), text="\x1d", ctrl=True)
    assert key(b"\x03") == TerminalKeyEvent(ord("c"), text="\x03", ctrl=True)


def test_kitty_key_reports():
    # kitty keyboard protocol: CSI unicode-key;mods u, ctrl = bit 4 in mods-1
    expect = TerminalKeyEvent(ord("\\"), text="\x1c", ctrl=True)
    assert key(b"\x1b[92;5u") == expect
    assert key(b"\x1b[93;5u").text == "\x1d"  # ctrl-]
    assert key(b"\x1b[99;5u").text == "\x03"  # ctrl-c
    assert key(b"\x9b92;5u").text == "\x1c"  # 8-bit CSI

    # extra modifiers alongside ctrl still count
    assert key(b"\x1b[92;13u").text == "\x1c"  # ctrl+super

    # alternate-key subparameters on the key field are ignored
    assert key(b"\x1b[92:124;5u").text == "\x1c"

    # explicit text-as-codepoints is decoded and takes precedence
    expect = TerminalKeyEvent(ord("a"), text="A", shift=True)
    assert key(b"\x1b[97;2;65u") == expect


def test_kitty_event_types():
    # press (1, or omitted) and repeat (2) count; release (3) does not
    assert key(b"\x1b[92;5:1u").text == "\x1c"
    assert key(b"\x1b[92;5:2u").text == "\x1c"
    assert key(b"\x1b[92;5:3u") is None


def test_modify_other_keys_reports():
    # xterm modifyOtherKeys: CSI 27;mods;key ~
    assert key(b"\x1b[27;5;92~").text == "\x1c"
    assert key(b"\x1b[27;5;93~").text == "\x1d"

    # some terminals report the resulting control code instead of the key
    expect = TerminalKeyEvent(ord("\\"), text="\x1c", ctrl=True)
    assert key(b"\x1b[27;5;28~") == expect


def test_modifier_decoding():
    assert key(b"\x1b[92;1u") == TerminalKeyEvent(92)
    assert key(b"\x1b[92u") == TerminalKeyEvent(92)
    assert key(b"\x1b[92;2u") == TerminalKeyEvent(92, shift=True)  # no text
    assert key(b"\x1b[92;3u") == TerminalKeyEvent(92, alt=True)


def test_non_key_chunks_ignored():
    assert key("hello") is None  # text chunks
    assert key(b"\x1b[A") is None  # arrow key
    assert key(b"\x1b[3;7R") is None  # cursor position reply
    assert key(b"\x1b[<35;1;2M") is None  # mouse report
    assert key(b"\x1b[?1u") is None  # kitty flags query reply
    assert key(b"\x1b[200~") is None  # bracketed paste marker
