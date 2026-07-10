from ok_serial._terminal_mode import TerminalMode


class TerminalWindow:
    """Rewrites terminal output to stay within a designated window region,
    eg. to leave room for a status bar or other "chrome".
    - sets a scrolling region with DECSTBM to the designated window
    - rewrites absolute cursor positioning to stay within the window
    - rewrites in-window DECSTBM to be a sub-region of the window
    - saves and restores in-window cursor position and mode for switching
      between windowed and non-windowed output (eg. when updating status bar)

    This class does not handle I/O but rewrites chunks (escapes or text runs)
    intended for the terminal or received from the terminal.
    """

    def __init__(self):
        self.window_region: tuple[int | None, int | None] = (None, None)
        self.scroll_region: tuple[int | None, int | None] = (None, None)
        self.cursor: tuple[int, int] | None = None
        self.mode: TerminalMode = TerminalMode()

    def cursor_query(self) -> list[bytes | str]:
        """Returns chunks to send to the terminal to query the cursor position.
        Replies are handled by .chunk_from_terminal(...) which updates .cursor.
        """
        return []

    def window_setup(self) -> list[bytes | str]:
        """Returns chunks to send to the terminal to enter/resume the window,
        based on .window_region, .scroll_region, .cursor, and .mode.
        (If .cursor is None, the cursor is moved to the top of the window.)
        """
        return []

    def chunk_to_window(self, chunk: bytes | str) -> bytes | str:
        """Translates an output chunk that should be kept inside the window.
        Returns a chunk to send to the terminal with positioning adjustments.
        Updates .scroll_region and .mode from the chunk; resets .cursor to None
        (accurate cursor position tracking is difficult).
        """
        return []

    def chunk_from_terminal(self, chunk: bytes | str) -> bytes | str | None:
        """Handles a chunk received from the terminal. If the chunk is a
        reply to a previous .cursor_query(...) call, updates .cursor and
        returns None, otherwise returns the chunk for upstream processing.
        """
        return []
