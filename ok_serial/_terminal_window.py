class TerminalWindow:
    """Rewrites terminal output to stay within a designated window region,
    eg. to leave room for a status bar or other "chrome".
    - sets a scrolling region with DECSTBM to the designated window
    - rewrites absolute cursor positioning to stay within the window
    - rewrites in-window DECSTBM to be a sub-region of the window
    - saves and restores in-window cursor position and mode when switching
      between windowed and non-windowed output (eg. when updating status bar)

    This class does not handle I/O but rewrites chunks (escapes or text runs)
    intended for the terminal or received from the terminal.
    """

    def __init__(self) -> None:
        pass

    def set_region(self, first_row: int, last_row: int) -> list[bytes | str]:
        """Sets window extent with 1-based row numbers; -1 = edge of screen.
        Returns any chunks to send directly to the terminal to prepare for the
        change (querying cursor position to restore after using DECSTBM).
        """
        return []

    def window_ready(self) -> bool:
        """Returns True if .chunk_to_window(...) is ready for use.
        Returns False if a previous cursor query (DSR/CPR) is still pending.
        (Check this again after .chunk_from_terminal(...) calls.)
        """
        return False

    def chunk_to_window(self, chunk: bytes | str) -> list[bytes | str]:
        """Translates an output chunk that should be kept inside the window.
        Returns modified chunks to send directly to the terminal, with
        absolute positioning adjusted and any cursor/mode restoration needed
        so all .chunk_to_window(...) output is logically continuous.
        """
        return []

    def chunk_to_terminal(self, chunk: bytes | str) -> list[bytes | str]:
        """Translates a NON window-boxed output chunk (eg. status bar update).
        Doesn't modify the chunk, but prepends a cursor query if necessary to
        save in-window state to restore in the next .chunk_to_window(...) call.

        Important: Unlike in-window state, non-windowed position and mode are
        NOT preserved when switching; each run of non-windowed output should
        start with mode and position setup (eg. DECSTR, CUP, SGR, "text").
        """
        return []

    def chunk_from_terminal(self, chunk: bytes | str) -> list[bytes | str]:
        return []
