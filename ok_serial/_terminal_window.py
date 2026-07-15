import re

from ok_serial._terminal_mode_tracker import TerminalModeTracker


INPUT_CODE_RX = re.compile(b"(?:\x1b\\[|\x9b)(?:(?P<cpr>\\d+;\\d+)R)")

OUTPUT_CODE_RX = re.compile(
    b"(?:\x1b\\[|\x9b)(?:(?P<cup>\\d*;?\\d*)H|(?P<decstbm>\\d*;?\\d*)r)"
)

QUERY_TIMEOUT = 1.0  # seconds from DSR to CPR


class TerminalWindow:
    """Rewrites terminal output to stay in a designated window region, eg.
    to leave room for a status bar or other independently updated "chrome".
    - sets a scrolling region (DECSTBM) to the designated window
    - translates cursor positioning to stay in the window
    - translates in-stream DECSTBM to be a sub-region of the window
    - saves and restores in-window cursor position and mode for switching
      between windowed and non-windowed output (eg. when updating status bar)

    This class does not do I/O itself but converts chunks (see TerminalChunker)
    on their way to and from the terminal.
    """

    def __init__(self) -> None:
        self._query_deadline: float | None = None
        self._setup_needed: bool = True
        self._window_cursor_xy: tuple[int, int] | None = None
        self._window_scroll_topbot: tuple[int | None, int | None] = (None, None)
        self._window_terminal_mode: TerminalModeTracker = TerminalModeTracker()
        self._window_topbot: tuple[int | None, int | None] = (None, None)

    def save_cursor_state(self, time: float) -> list[bytes | str]:
        """Saves window cursor position and terminal mode (but not content).
        - time: current clock time (some consistent epoch)

        Call before making non-window terminal writes (eg. status bar update).
        Returns chunks (if any) to send to the terminal now (eg. cursor query).
        The next call to .convert_output_chunk() will restore the saved state.
        """
        assert time >= 0.0, time
        self._setup_needed = True
        if self.is_output_ready(time):
            return []
        else:
            self._query_deadline = time + QUERY_TIMEOUT
            return [b"\x1b[6n"]

    def is_output_ready(self, time: float) -> bool:
        """Returns .convert_output_chunk() is ready (no query in flight, etc).
        - time: current clock time (some consistent epoch)

        If False, pause output but continue calling .convert_input_chunk().
        """
        return not (self._query_deadline and time < self._query_deadline)

    def convert_output_chunk(
        self, chunk: bytes | str, time: float
    ) -> list[bytes | str]:
        """Transforms a chunk to stay within the window region.
        - chunk: escape code or text to window-restrict
        - time: current clock time (some consistent epoch)

        Returns a list of chunks for direct output:
        - prepends codes to setup/restore cursor position and mode
        - transforms/clips absolute cursor positioning for window position
        - other chunks are returned as-is
        REQUIRES .is_output_ready() is True
        """
        assert self.is_output_ready(time), (time, self._query_deadline)
        out = []
        if self._window_setup_needed:
            reg = [b"" if v is None else b"%d" % v for v in self._window_topbot]
            out.append(b"\x1b[%sr" % b";".join(reg))  # DECSTBM - set margins
            # TODO: move cursor
            out.extend(self._window_terminal_mode.mode_chunks())
            self._window_setup_needed = False

        # TODO: translate absolute to relative position
        # TODO: handle DECSTBM in output stream
        self._window_cursor_xy = None
        out.append(chunk)
        return out

    def convert_input_chunk(self, chunk: bytes | str) -> list[bytes | str]:
        """Processes a chunk received from the terminal.
        - chunk: escape code or text received from terminal

        Returns a list of chunks (if any) to be handled by application:
        - locally generated cursor query replies are consumed
        - other cursor query replies are translated to window coordinates
        - other chunks are returned as-is
        """
        if not isinstance(chunk, bytes):
            return [chunk]
        if not (match := INPUT_CODE_RX.match(chunk)):
            return [chunk]

        code = match.lastgroup
        if code == "cpr":
            row, col = map(int, match[code].split(b";"))
            top, bot = self._window_topbot
            row = min(row, bot) if bot is not None else row
            row = max(1, row - top + 1) if top is not None else row
            if self._query_pending is not None:
                self._window_cursor_xy = (col, row)
                self._query_pending = None
                return []
            else:
                return [b"\x1b[%d;%dR" % (row, col)]
        else:
            assert False, (code, match.groupdict())  # unknown named group?
