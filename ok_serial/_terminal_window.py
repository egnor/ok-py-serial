from ok_serial._terminal_mode_saver import TerminalModeSaver


class TerminalWindow:
    """Rewrites a terminal output stream to stay in a designated window region,
    eg. to leave room for a status bar or other independently updated "chrome".
    - sets a scrolling region (DECSTBM) to the designated window
    - translates cursor positioning to stay within the window
    - translates in-stream DECSTBM to be a sub-region of the window
    - saves and restores in-window cursor position and mode for switching
      between windowed and non-windowed output (eg. when updating status bar)

    This class does not handle I/O but rewrites chunks (escapes or text runs)
    intended for the terminal or received from the terminal.

    Typical usage:
    - optionally, capture initial cursor position
      - send .saved_cursor() output to the terminal
      - pass input to .handle_input_chunk(...) until .saved_cursor is set
    - set .window_region as desired
    - pass in-window output through .translate_window_chunk(...) before output
    - to make updates outside the window (eg. status bar):
      - send .saved_cursor() output to the terminal
      - write codes directly to the terminal (eg. DECSTR+CUP+SGR+text)
      - pass input to .handle_input_chunk(...) until .saved_cursor is set
      - the next .translate_window_chunk(...) will restore cursor, mode, etc.
    """

    def __init__(self):
        self.reinit_needed: bool = True
        self.window_cursor: tuple[int, int] | None = None
        self.saved_mode: TerminalModeSaver = TerminalModeSaver()
        self.scroll_region: tuple[int | None, int | None] = (None, None)
        self.window_region: tuple[int | None, int | None] = (None, None)

    def translate_window_chunk(self, chunk: bytes | str) -> list[bytes | str]:
        """Transforms output that should be contained inside the window.
        Updates .window_scroll and .saved_mode; resets .window_cursor to None.
        Returns chunks to send to the terminal:
        - setup/restore cursor/mode/scroll-region if needed
        - the original chunk with transformed coordinates
        """
        return []

    def cursor_save(self) -> list[bytes | str]:
        """If .window_cursor is None, returns a chunk to query the terminal
        for the current cursor position, otherwise returns [].
        """
        return []

    def handle_input_chunk(self, chunk: bytes | str) -> bytes | str | None:
        """Handles a chunk received from the terminal. If the chunk is a
        reply to previous .query_cursor(...), updates .window_cursor and
        returns None, otherwise returns the chunk for further processing.
        """
        return None
