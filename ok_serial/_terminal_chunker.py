import re


class TerminalChunker:
    """Breaks VTxxx data into output characters and control sequences."""

    PARSE_RX = re.compile(
        # group 1: string of complete UTF-8 code points
        # https://en.wikipedia.org/wiki/UTF-8#Description
        b"((?:"
        b"[\x20-\x7e]|"
        b"[\xc0-\xdf][\x80-\xbf]|"
        b"[\xe0-\xef][\x80-\xbf]{2}|"
        b"[\xf0-\xf7][\x80-\xbf]{3}"
        b")+)|"
        # group 2: *partial* UTF-8 code point at end of data
        b"([\xc0-\xf7][\x80-\xbf]*$)|"
        # group 3: one complete VTxxx control sequence
        # https://vt100.net/emu/dec_ansi_parser
        b"("
        b"(?:\x1b\\[|\x9b)[\x20-\x3f]*[\x40-\x7e]|"  # CSI
        b"(?:\x1b[\x50\x58\\]-\x5f]|[\x98\x9d-\x9f])"  # DCS/SOS/OSC/PM/APC...
        b"[\x20-\x7f]*(?:\x07|\x9c|\x1b\\\\)|"  # ...end DCS/SOS/OSC/PM/APC
        b"\x1b[\x30-\x4f\x51-\x57\x59\x5a\x60-\x7e]"  # ESC-char controls
        b")|"
        # group 4: *partial* VTxxx control sequence at end of data
        b"("
        b"\x1b$|"  # ESC by itself
        b"(?:\x1b\\[|\x9b)[\x20-\x3f]*$|"  # CSI
        b"(?:\x1b[\x50\x58\\]-\x5f]|[\x98\x9d-\x9f])[\x20-\x7f]*\x1b?$"  # etc
        b")|"
        # group 5: any other byte (control char, invalid, etc)
        b"([\x00-\xff])"
    )

    TIMEOUT = 0.1  # seconds to pause before giving up on partial data

    def __init__(self) -> None:
        self._partial = bytearray()
        self._partial_deadline = 0.0
        self._chunks: list[str | bytes] = []

    def add_data(self, data: bytes, data_time: float) -> None:
        """Adds terminal data with timestamp. Use data=b"" to mark idle time."""

        if not data and self._partial and data_time > self._partial_deadline:
            self._chunks.append(bytes(self._partial[:1]))
            self._partial = self._partial[1:]

        self._partial.extend(data)
        pos = 0
        while pos < len(self._partial):
            match = self.PARSE_RX.match(self._partial, pos)
            assert match, self._partial[pos:]
            chars, char_part, esc, esc_part, other = match.groups()
            if chars:
                self._chunks.append(chars.decode())
                pos += len(chars)
            elif esc:
                self._chunks.append(esc)
                pos += len(esc)
            elif other:
                self._chunks.append(other)
                assert len(other) == 1, other
                pos += 1
            else:
                self._partial = self._partial[pos:]
                self._partial_deadline = data_time + self.TIMEOUT
                assert self._partial in (char_part, esc_part), match.groups()
                return

        self._partial.clear()
        self._partial_deadline = 0.0

    def read_chunks(self) -> list[str | bytes]:
        """Returns accumulated data so far:
        - str for well formed UTF-8 strings
        - bytes for control characters, escape sequences, or invalid data

        Partial but otherwise valid UTF-8 or terminal codes stay buffered
        more data arrives they time out so they can be returned as one chunk.
        """
        out, self._chunks = self._chunks, []
        return out

    @property
    def partial_deadline(self) -> float | None:
        """When incoming partial data will be flushed, None if no such"""
        return self._partial_deadline if self._partial else None
