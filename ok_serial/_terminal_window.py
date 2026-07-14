import re

from ok_serial._terminal_mode_saver import TerminalModeSaver


INPUT_CODE_RX = re.compile(b"(?:\x1b\\[|\x9b)(?:(?P<cpr>\\d+;\\d+)R)")

OUTPUT_CODE_RX = re.compile(b"(?:\x1b\\[|\x9b)(\\d+);(\\d+)H")


class TerminalWindow:
    """Rewrites a terminal output stream to stay in a designated window region,
    eg. to leave room for a status bar or other independently updated "chrome".
    - sets a scrolling region (DECSTBM) to the designated window
    - translates cursor positioning to stay within the window
    - translates in-stream DECSTBM to be a sub-region of the window
    - saves and restores in-window cursor position and mode for switching
      between windowed and non-windowed output (eg. when updating status bar)

    This class does not do I/O directly but processes chunks (escape bytes or
    text str, see TerminalChunker) on their way to and from the terminal.
    """

    def __init__(self) -> None:
        self.setup_needed: bool = True
        self.query_pending: bool = False
        self.cursor_xy: tuple[int, int] | None = None
        self.terminal_mode: TerminalModeSaver = TerminalModeSaver()
        self.scroll_topbot: tuple[int | None, int | None] = (None, None)
        self.window_topbot: tuple[int | None, int | None] = (None, None)

    def translate_window_chunk(self, chunk: bytes | str) -> list[bytes | str]:
        """Transforms output that should be contained within the window.
        Updates .scroll_topbot and .saved_mode; resets .cursor_xy to None.
        Returns chunks to send to the terminal:
        - setup/restore cursor/mode/scroll-region if .setup_needed
        - the original chunk with transformed coordinates
        """
        out: list[bytes | str] = []
        # TODO XXX
        return out

    def capture_window_state(self) -> list[bytes | str]:
        """Queries cursor position for restoration after
        - Returns chunks to send to the terminal to query the cursor (DSR 6)
        - Sets .setup_needed so the next .translate_window_chunk(...) will
          restore cursor/mode/scroll-region
        """
        self.setup_needed = True
        self.query_pending = not self.cursor_xy
        return [b"\x1b[6n"] if self.query_pending else []

    def handle_input_chunk(self, chunk: bytes | str) -> bytes | str | None:
        """Handles a chunk received from the terminal.
        If .query_pending and the chunk is a cursor position report, updates
        .cursor_xy, resets .query_pending to False, and returns None.
        Otherwise, returns the original chunk for further processing.
        """

        if (
            self.query_pending
            and isinstance(chunk, bytes)
            and (match := INPUT_CODE_RX.match(chunk))
        ):
            code = match.lastgroup
            if code == "cpr":
                row, col = map(int, match[code].split(b";"))
                self.cursor_xy = (col, row)
                self.query_pending = False
                return None
            else:
                assert False, (code, match.groupdict())  # unknown named group?

        return chunk
