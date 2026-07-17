import re
from typing import Literal

from ok_serial._terminal_mode_tracker import TerminalModeTracker

# regexp to match vtxxx command codes with absolute row numbers
# https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
OUT_CODE_RX = re.compile(
    # ESC codes
    b"\x1b(?:"
    b"(?P<ris>c)"  # reset to initial state
    b")|"
    # CSI codes
    b"(?:\x1b\\[|\x9b)(?:"  # CSI
    b"(?P<cpr>6)n|"  # cursor position request
    b"(?P<cup>[0-9;]*)H|"  # cursor position
    b"(?P<decom>\\?6[hl]|"  # DEC origin mode
    b"(?P<decsed>\\?[0123]?)J|"  # DEC selective erase
    b"(?P<decstbm>[0-9;]*)r|"  # DEC set top and bottom margins
    b"(?P<decstr>!)p|"  # DEC soft terminal reset
    b"(?P<decxxra>[0-9;]*(?:\\$[rtvxz{]|\\*y))|"  # DEC rectangular area ops
    b"(?P<ed>[0123]?)J|"  # erase display
    b"(?P<vpa>[0-9;]*)d|"  # vertical position absolute
    b"(?P<hvp>[0-9;]*)f|"  # horizontal and vertical position
    b"(?P<xtreportsgr>[0-9;]*)#\\"  # XTerm report SGR in rectangle
    b")"
)

# regexp to match vtxxx response codes with absolute row numbers
IN_CODE_RX = re.compile(
    # CSI codes
    b"(?:\x1b\\[|\x9b)(?:"  # CSI
    b"(?P<cpr>[0-9]+;[0-9]+)R|"  # cursor position report
    b"(?P<decxcpr>[0-9]+;[0-9]+;[0-9]+)R|"  # cursor position report
    b'(?P<decrpde>[0-9]+;[0-9]+;[0-9]+;[0-9]+;[0-9]+)"w'  # report disp. extent
    b")|"
    # DCS codes
    b"(?:\x1bP|\x90)(?:"  # DCS
    b"(?P<decrpss_decstbm>1\\$r[0-9]+;[0-9]+r)"  # report selection of DECSTBM
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
        self._query_deadlines: list[float] = []
        self._setup_needed: bool = True
        self._virt_rowcol: tuple[int, int] | Literal["unk", "wait"] = "unk"
        self._virt_scroll_topbot: tuple[int | None, int | None] = (None, None)
        self._virt_terminal_mode: TerminalModeTracker = TerminalModeTracker()
        self._window_topbot: tuple[int | None, int | None] = (None, None)

    def save_cursor_state(self, time: float) -> list[bytes | str]:
        """Saves window cursor position and terminal mode (but not content).
        - time: clock time in seconds (any consistent epoch)

        Call before making outside terminal writes (eg. status bar update).
        Returns chunks (if any) to send to the terminal (eg. cursor query).
        The next .convert_output_chunk() will restore the saved state.
        """
        assert time >= 0.0, time
        self._setup_needed = True
        if self._virt_rowcol == "unk":
            self._query_deadlines.append(time + QUERY_TIMEOUT)
            self._virt_rowcol = "wait"
            return [b"\x1b[6n"]
        return []

    def is_output_ready(self, time: float) -> bool:
        """Returns .convert_output_chunk() readiness (no query in flight, etc).
        - time: clock time in seconds (any consistent epoch)

        If False, pause output but continue calling .convert_input_chunk().
        """
        while self._query_deadlines and self._query_deadlines[0] < time:
            del self._query_deadlines[0]
        return self._virt_rowcol != "wait" or not self._query_deadlines

    def convert_output_chunk(
        self, chunk: bytes | str, time: float
    ) -> list[bytes | str]:
        """Transforms a chunk to stay in the window region.
        REQUIRES .is_output_ready().
        - chunk: escape code or text to window-restrict
        - time: clock time in seconds (any consistent epoch)

        Returns a list of chunks for direct output:
        - prepends codes to setup/restore cursor position and mode
        - transforms/clips absolute cursor positioning for window position
        - other chunks are returned as-is
        """
        assert self.is_output_ready(time), (time, self._query_deadlines)
        self._virt_terminal_mode.add_chunk(chunk)

        out: list[bytes | str] = [chunk]
        if isinstance(chunk, bytes) and (rxm := OUT_CODE_RX.fullmatch(chunk)):
            code = rxm.lastgroup
            assert code, rxm.groupdict()
            # body = rxm[code]

            if code == "ris":
                self._setup_needed = True
                self._virt_rowcol = (0, 0)
                self._virt_scroll_topbot = (None, None)
                out.append(b"\x1b[2J")  # clear screen
            elif code == "cup":
                # TODO: modify coordinates
                pass
            elif code == "decom":
                pass  # recorded in _virt_terminal_mode, NOT passed through
            elif code in ("decsed", "ed"):
                out.extend(self.save_cursor_state(time))
                # TODO: emulate screen erase with line erase
            elif code == "decstbm":
                # TODO: emulate margins (and move cursor!)
                self._setup_needed = True
                self._virt_rowcol = (0, 0)
            elif code == "decstr":
                self._virt_scroll_topbot = (None, None)
                out.extend(self.save_cursor_state(time))  # re-setup w/ reset
            elif code in ("decxxra", "xtreportsgr"):
                # TODO: modify coordinates
                pass
            elif code == "vpa":
                # TODO: modify coordinates
                pass
            elif code == "hvp":
                # TODO: modify coordinates
                pass
            else:
                assert False, code  # unknown named group?
        else:
            self._virt_rowcol = "unk"  # cannot predict, invalidate
            out.append(chunk)  # pass through

        if self._setup_needed:
            out = self._terminal_setup_chunks() + out
            self._setup_needed = False

        return out

    def convert_input_chunk(self, chunk: bytes | str) -> list[bytes | str]:
        """Processes a chunk received from the terminal.
        - chunk: escape code or text received from terminal

        Returns a list of chunks (if any) to be handled by application:
        - locally generated cursor query replies are consumed
        - other cursor query replies are translated to window coordinates
        - other chunks are returned as-is
        """
        if isinstance(chunk, bytes) and (rxm := IN_CODE_RX.fullmatch(chunk)):
            code = rxm.lastgroup
            if code == "cpr":
                t_row, col = [int(v) for v in rxm[code].split(b";")]
                w_row = self._virt_row_from_terminal(t_row)
                del self._query_deadlines[:1]
                if self._virt_rowcol == "wait" and not self._query_deadlines:
                    self._virt_rowcol = (w_row, col)
                    return []
                return [b"\x1b[%d;%dR" % (w_row, col)]
            else:
                assert False, (code, rxm.groupdict())  # unknown named group?
        else:
            return [chunk]  # pass through

    def _terminal_setup_chunks(self) -> list[bytes | str]:
        margins = [b"" if v is None else b"%d" % v for v in self._window_topbot]
        out: list[bytes | str] = [b"\x1b[%sr" % b";".join(margins)]  # DECSTBM

        save_origin_mode = self._virt_terminal_mode.dec_modes[6]
        self._virt_terminal_mode.dec_modes[6] = b"l"
        out.extend(self._virt_terminal_mode.mode_chunks())
        self._virt_terminal_mode.dec_modes[6] = save_origin_mode

        if isinstance(self._virt_rowcol, tuple):
            w_row, col = self._virt_rowcol
            t_row = self._terminal_row_from_window(w_row)
            out.append(b"\x1b[%d;%dH" % (t_row, col))  # CUP
        return out

    def _terminal_row_from_window(self, row: int) -> int:
        w_top, w_bot = self._window_topbot
        w_top, w_bot = w_top or 1, w_bot or 9999
        if self._virt_terminal_mode.dec_modes[6] == b"h":  # DECOM set
            t_top, t_bot = self._virt_scroll_topbot
            t_top, t_bot = t_top or w_top, t_bot or w_bot
        else:
            t_top, t_bot = 1, 9999

        t_row = row + w_top - t_top
        return max(1, min(w_bot - w_top + 1, t_bot - t_top + 1, t_row))

    def _virt_row_from_terminal(self, row: int) -> int:
        # TODO: implement
        return row
