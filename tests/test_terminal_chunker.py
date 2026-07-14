"""Unit tests for ok_serial._terminal_chunker."""

from ok_serial._terminal_chunker import TerminalChunker, CHUNK_TIMEOUT


def chunk_all(data: bytes) -> list[str | bytes]:
    """Feeds all data in one shot at t=0 and returns the chunks."""
    chunker = TerminalChunker()
    chunker.add_data(data, 0.0)
    return chunker.chunks


def test_plain_text():
    assert chunk_all(b"hello world") == ["hello world"]


def test_multibyte_utf8():
    # 2-, 3- and 4-byte code points all decode as one str chunk
    assert chunk_all("café — 😀".encode()) == ["café — 😀"]


def test_control_chars_are_bytes():
    # C0 controls (newline, tab, DEL) come back as raw bytes, not text
    assert chunk_all(b"a\nb\tc\x7f") == ["a", b"\n", "b", b"\t", "c", b"\x7f"]


def test_invalid_utf8_falls_through_to_bytes():
    # overlong encoding, a surrogate, and a lone continuation byte are each
    # emitted one byte at a time (none of these decode under strict UTF-8)
    assert chunk_all(b"\xc0\x80") == [b"\xc0", b"\x80"]
    assert chunk_all(b"\xed\xa0\x80") == [b"\xed", b"\xa0", b"\x80"]
    assert chunk_all(b"x\xff") == ["x", b"\xff"]


def test_csi_sequence():
    # ESC [ ... final, kept whole and returned as bytes
    assert chunk_all(b"\x1b[1;31mhi\x1b[0m") == [
        b"\x1b[1;31m",
        "hi",
        b"\x1b[0m",
    ]


def test_eight_bit_csi():
    assert chunk_all(b"\x9b2J") == [b"\x9b2J"]


def test_osc_string_with_terminators():
    # OSC ended by BEL, and DCS ended by ST (ESC \)
    assert chunk_all(b"\x1b]0;title\x07") == [b"\x1b]0;title\x07"]
    assert chunk_all(b"\x1bP1$r0m\x1b\\") == [b"\x1bP1$r0m\x1b\\"]


def test_esc_char_control():
    # single-char escapes: ESC c (reset), ESC 7 (save cursor)
    assert chunk_all(b"\x1bc\x1b7") == [b"\x1bc", b"\x1b7"]


def test_ss3_and_ss2_keyboard_input():
    # function/keypad keys arrive as SS3/SS2 + one char; kept as a unit even
    # though the DEC output parser would treat ESC O / ESC N as terminal
    assert chunk_all(b"\x1bOP") == [b"\x1bOP"]  # F1
    assert chunk_all(b"\x1bOA") == [b"\x1bOA"]  # up-arrow (application mode)
    assert chunk_all(b"\x1bNx") == [b"\x1bNx"]  # SS2
    assert chunk_all(b"\x8fP") == [b"\x8fP"]  # 8-bit SS3


def test_esc_intermediate_final():
    # ESC + intermediate(s) + final: charset designation and the DEC
    # alignment test, each kept whole
    assert chunk_all(b"\x1b(B") == [b"\x1b(B"]  # designate ASCII into G0
    assert chunk_all(b"\x1b#8") == [b"\x1b#8"]  # DECALN


def test_trailing_newline_after_high_byte():
    # regression: `$` matches before a trailing newline, `\Z` does not, so a
    # lead byte followed by newline must not be mistaken for a partial
    assert chunk_all(b"\xdd\n") == [b"\xdd", b"\n"]


def test_split_utf8_reassembled():
    # a 4-byte emoji split across two reads (within the timeout) rejoins
    chunker = TerminalChunker()
    data = "ok😀".encode()
    chunker.add_data(data[:4], 0.0)
    assert chunker.chunks == ["ok"]  # the partial emoji is held back
    chunker.add_data(data[4:], 0.05)
    assert chunker.chunks == ["ok", "😀"]


def test_split_csi_reassembled():
    chunker = TerminalChunker()
    chunker.add_data(b"\x1b[1;", 0.0)
    assert chunker.chunks == []  # incomplete CSI held back
    chunker.add_data(b"31m", 0.05)
    assert chunker.chunks == [b"\x1b[1;31m"]


def test_partial_times_out_to_bytes():
    # a lone ESC (or any stuck partial) is flushed a byte at a time once the
    # deadline passes with no further data
    chunker = TerminalChunker()
    chunker.add_data(b"\x1b", 0.0)
    assert chunker.chunks == []  # held, waiting for the rest
    chunker.add_data(b"", chunker.data_deadline + 1.0)  # idle past the deadline
    assert chunker.chunks == [b"\x1b"]


def test_deadline_advances_with_partial():
    chunker = TerminalChunker()
    chunker.add_data(b"hi", 10.0)
    # nothing pending -> a long deadline; a partial -> a short one
    assert chunker.data_deadline > 100.0
    chunker.add_data(b"\x1b[", 20.0)
    assert chunker.data_deadline == 20.0 + CHUNK_TIMEOUT
