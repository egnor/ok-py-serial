import re

from ok_serial._terminal_mode_tracker import TerminalModeTracker


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
        self.chunks_to_transmit: list[bytes | str] = []
        self.chunks_to_receive: list[bytes | str] = []

        self._query_pending: bool = False
        self._window_setup_needed: bool = True
        self._window_cursor_xy: tuple[int, int] | None = None
        self._window_scroll_topbot: tuple[int | None, int | None] = (None, None)
        self._window_terminal_mode: TerminalModeTracker = TerminalModeTracker()
        self._window_topbot: tuple[int | None, int | None] = (None, None)

    def add_window_chunk(self, chunk: bytes | str) -> None:
        """Collects a window-boxed chunk for transmission to the terminal."""
        if self._window_setup_needed:
            # TODO: set up scrolling region
            # TODO: move cursor
            mode_setup = self._window_terminal_mode.mode_chunks()
            self.chunks_to_transmit.extend(mode_setup)
            self._window_setup_neede = False
        # TODO: translate absolute to relative position
        self.chunks_to_transmit.append(chunk)

    def save_window_state(self) -> None:
        """Queries cursor position for restoration after
        - Returns chunks to send to the terminal to query the cursor (DSR 6)
        - Sets .setup_needed so the next .translate_window_chunk(...) will
          restore cursor/mode/scroll-region
        """
        self._setup_needed = True
        if not self._cursor_xy and not self._query_pending:
            self._query_pending = True
            self.chunks_to_transmit.append(b"\x1b[6n")

    def handle_input_chunk(self, chunk: bytes | str) -> bytes | str | None:
        """Handles a chunk received from the terminal.
        If .query_pending and the chunk is a cursor position report, updates
        .cursor_xy, resets .query_pending to False, and returns None.
        Otherwise, returns the original chunk for further processing.
        """
        if not self._query_pending:
            return chunk
        if not isinstance(chunk, bytes):
            return chunk
        if not (match := INPUT_CODE_RX.match(chunk)):
            return chunk

        code = match.lastgroup
        if code == "cpr":
            row, col = map(int, match[code].split(b";"))
            self._cursor_xy = (col, row)
            self._query_pending = False
            return None
        else:
            assert False, (code, match.groupdict())  # unknown named group?

        return chunk
