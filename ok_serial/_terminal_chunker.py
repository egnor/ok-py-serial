import re

CHUNK_RX = re.compile(
    # group 1: well-formed UTF-8 code points -- what str.decode() accepts
    # Grammar: https://datatracker.ietf.org/doc/html/rfc3629#section-4
    b"((?:"
    b"[\x20-\x7e]|"  # printable ASCII (other 1-byte are controls, group 5)
    b"[\xc2-\xdf][\x80-\xbf]|"  # 2-byte
    # 3-byte, no overlong, no UTF-16 surrogates (U+D800..U+DFFF)
    b"\xe0[\xa0-\xbf][\x80-\xbf]|[\xe1-\xec][\x80-\xbf]{2}|"
    b"\xed[\x80-\x9f][\x80-\xbf]|[\xee-\xef][\x80-\xbf]{2}|"
    # 4-byte, no overlong, no code points > U+10FFFF
    b"\xf0[\x90-\xbf][\x80-\xbf]{2}|[\xf1-\xf3][\x80-\xbf]{3}|"
    b"\xf4[\x80-\x8f][\x80-\xbf]{2}"
    b")+)|"
    # group 2: incomplete-but-valid UTF-8 prefix at end of data
    b"("
    b"[\xc2-\xf4]|"
    b"\xe0[\xa0-\xbf]|[\xe1-\xec][\x80-\xbf]|"
    b"\xed[\x80-\x9f]|[\xee-\xef][\x80-\xbf]|"
    b"\xf0[\x90-\xbf][\x80-\xbf]?|[\xf1-\xf3][\x80-\xbf]{1,2}|"
    b"\xf4[\x80-\x8f][\x80-\xbf]?"
    b")\\Z|"
    # group 3: one complete VTxxx control sequence
    # https://vt100.net/emu/dec_ansi_parser
    b"("
    b"(?:\x1b\\[|\x9b)[\x20-\x3f]*[\x40-\x7e]|"  # CSI
    b"(?:\x1b[\x50\x58\\]-\x5f]|[\x90\x98\x9d-\x9f])"  # DCS/SOS/OSC/PM/APC
    b"[\x20-\x7f]*(?:\x07|\x9c|\x1b\\\\)|"  # ...end DCS/SOS/OSC/PM/APC
    b"(?:\x1b[\x4e\x4f]|[\x8e\x8f])[\x20-\x7e]|"  # SS2/SS3 + char
    b"\x1b[\x20-\x2f]+[\x30-\x7e]|"  # ESC + intermediates + final (charset)
    b"\x1b[\x30-\x4d\x51-\x57\x59\x5a\x60-\x7e]"  # ESC-char controls
    b")|"
    # group 4: *partial* VTxxx control sequence at end of data
    b"("
    b"\x1b\\Z|"  # ESC by itself
    b"(?:\x1b[\x4e\x4f]|[\x8e\x8f])\\Z|"  # SS2/SS3 awaiting char
    b"\x1b[\x20-\x2f]+\\Z|"  # ESC + intermediates awaiting final
    b"(?:\x1b\\[|\x9b)[\x20-\x3f]*\\Z|"  # CSI
    b"(?:\x1b[\x50\x58\\]-\x5f]|[\x90\x98\x9d-\x9f])[\x20-\x7f]*\x1b?\\Z"
    b")|"
    # group 5: any other byte (control char, invalid, etc)
    b"([\x00-\xff])"
)

CHUNK_TIMEOUT = 0.1  # seconds to pause before giving up on partial data


class TerminalChunker:
    """Breaks VTxxx data into output characters and control sequences."""

    def __init__(self) -> None:
        self.chunks: list[str | bytes] = []
        self.data_deadline = 0.0
        self._partial = bytearray()

    def add_data(self, data: bytes, data_time: float) -> None:
        """Adds terminal data with timestamp. Use data=b"" to mark idle time."""

        if not data and self._partial and data_time > self.data_deadline:
            self.chunks.append(bytes(self._partial[:1]))
            self._partial = self._partial[1:]

        self._partial.extend(data)
        pos = 0
        while pos < len(self._partial):
            match = CHUNK_RX.match(self._partial, pos)
            assert match, self._partial[pos:]
            chars, char_part, esc, esc_part, other = match.groups()
            if chars:
                # group 1 only matches well-formed UTF-8, so this can't raise
                self.chunks.append(chars.decode())
                pos += len(chars)
            elif esc:
                self.chunks.append(esc)
                pos += len(esc)
            elif other:
                self.chunks.append(other)
                assert len(other) == 1, other
                pos += 1
            else:
                self._partial = self._partial[pos:]
                self.data_deadline = data_time + CHUNK_TIMEOUT
                assert self._partial in (char_part, esc_part), match.groups()
                return

        self._partial.clear()
        self.data_deadline = data_time + 3600.0  # long timeout


def chunk_to_bytes(chunk: str | bytes):
    """Returns the data-stream bytes for a TerminalChunker-type chunk."""
    assert isinstance(chunk, (str, bytes)), chunk
    return chunk if isinstance(chunk, bytes) else chunk.encode()
