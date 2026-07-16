import re

from ok_serial._terminal_mode_tracker import TerminalModeTracker

# regexp to match vtxxx command codes with absolute row numbers
# https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
OUTPUT_CODE_RX = re.compile(
    b"(?:\x1b\\[|\x9b)(?:"  # CSI
    b"(?P<cup>[0-9;]*)H|"
    b"(?P<decxxra>[0-9;]*(?:\\$[rtvxz{]|\\*y))|"
    b"(?P<decsed>\\?[0123]?)J|"
    b"(?P<decstbm>[0-9;]*)r|"
    b"(?P<ed>[0123]?)J|"
    b"(?P<vpa>[0-9;]*)d|"
    b"(?P<hvp>[0-9;]*)f|"
    b"(?P<xtreportsgr>[0-9;]*)#\\|"
    b")"
)

# regexp to match vtxxx response codes with absolute row numbers
INPUT_CODE_RX = re.compile(
    # CSI codes
    b"(?:\x1b\\[|\x9b)(?:"  # CSI
    b"(?P<cpr>[0-9]+;[0-9]+)R|"
    b'(?P<decrpde>[0-9]+;[0-9]+;[0-9]+;[0-9]+;[0-9]+)"w'
    b")"
    # DCS codes
    b"(?:\x1bP|\x90)(?:"  # DCS
    b"(?P<decrpss_decstbm>1\\$r[0-9]+;[0-9]+r)"
    b")(?:\x07|\x1b\\\\|\x9c)"  # ST
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
        self._window_cursor_rowcol: tuple[int, int] | None = None
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
        if self._window_cursor_rowcol or self._query_deadline:
            return []
        else:
            self._query_deadline = time + QUERY_TIMEOUT
            return [b"\x1b[6n"]

    def is_output_ready(self, time: float) -> bool:
        """Returns .convert_output_chunk() is ready (no query in flight, etc).
        - time: current clock time (some consistent epoch)

        If False, pause output but continue calling .convert_input_chunk().
        """
        return (
            not self._setup_needed
            or self._window_cursor_rowcol is not None
            or not self._query_deadline
            or time >= self._query_deadline
        )

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
        out: list[bytes | str] = []
        if self._setup_needed:
            reg = [b"" if v is None else b"%d" % v for v in self._window_topbot]
            out.append(b"\x1b[%sr" % b";".join(reg))  # DECSTBM - set margins
            out.extend(self._window_terminal_mode.mode_chunks())
            if self._window_cursor_rowcol:
                row, col = self._window_cursor_rowcol
                row = self._window_to_terminal_row(row)
                cup_args = [b"" if v <= 1 else b"%d" % v for v in (row, col)]
                out.append(b"\x1b[" + b";".join(cup_args))  # CUP - move cursor

            self._setup_needed = False

        # TODO: translate absolute to relative position
        # TODO: handle DECSTBM in output stream
        # TODO: handle clear-screen

        self._window_cursor_rowcol = None
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
            row, col = [int(v) for v in match[code].split(b";")]
            row = self._terminal_to_window_row(row)
            if self._query_deadline is not None:
                self._window_cursor_rowcol = (row, col)
                self._query_deadline = None
                return []
            else:
                return [b"\x1b[%d;%dR" % (row, col)]
        else:
            assert False, (code, match.groupdict())  # unknown named group?

    def _window_to_terminal_row(self, row: int) -> int:
        w_top, w_bot = self._window_topbot
        w_top, w_bot = w_top or 1, w_bot or 9999
        if self._terminal_mode_tracker.dec_modes[6] == "h":  # DECOM set
            t_top, t_bot = self._window_scroll_topbot
            t_top, t_bot = t_top or w_top, t_bot or w_bot
        else:
            t_top, t_bot = 1, 9999

        t_row = row + w_top - t_top
        return max(1, min(w_bot - w_top + 1, t_bot - t_top + 1, t_row))

    def _terminal_to_window_row(self, row: int) -> int:
        pass
