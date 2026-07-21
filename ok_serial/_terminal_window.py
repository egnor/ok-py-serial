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
    b"(?P<decstr>)!p|"  # DEC soft terminal reset
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
        self._virt_rowcol: tuple[int | None, int | None] = (None, None)
        self._virt_scroll_topbot: tuple[int | None, int | None] = (None, None)
        self._virt_terminal_mode: TerminalModeTracker = TerminalModeTracker()
        self._window_state: Literal["active", "querying", "saved"] = "active"
        self._window_topbot: tuple[int | None, int | None] = (None, None)

    def checkpoint_state(
        self,
        time: float,
        topbot: tuple[int | None, int | None] | None = None,
    ) -> list[bytes | str]:
        """Saves and optionally reconfigures window state for later restoration.
        - time: clock time in seconds (any consistent epoch)
        - topbot: if not None, new (top, bottom) of window (1-based, inclusive)

        Returns chunks for direct terminal output (eg. cursor query) if any.
        The next .convert_window_chunk() will restore saved terminal state.
        """
        assert time >= 0.0, time
        self._window_topbot = topbot or self._window_topbot
        if self._window_state == "active":
            if all(self._virt_rowcol):
                self._window_state = "saved"
            else:
                self._window_state = "querying"
                self._query_deadlines.append(time + QUERY_TIMEOUT)
                return [b"\x1b[6n"]
        return []

    def is_window_ready(self, time: float) -> bool:
        """Returns .convert_window_chunk(...) readiness (no query in flight).
        - time: clock time in seconds (any consistent epoch)

        Return value
        - True: free to call .convert_window_chunk() / .convert_input_chunk().
        - False: pause output, but keep calling .convert_input_chunk().
        """
        while self._query_deadlines and self._query_deadlines[0] < time:
            del self._query_deadlines[0]
        if self._window_state == "querying" and not self._query_deadlines:
            self._window_state = "saved"  # timed out
        return self._window_state != "querying"

    def convert_window_chunk(
        self, chunk: bytes | str, time: float
    ) -> list[bytes | str]:
        """Transforms a chunk to stay in the window region.
        REQUIRES .is_window_ready().
        - chunk: escape code or text to window-restrict
        - time: clock time in seconds (any consistent epoch)

        Returns a list of chunks for direct terminal output:
        - codes to setup/restore cursor position and mode if needed
        - absolute cursor positioning transformed/clipped for the window
        - other chunks are returned as-is
        """
        assert self._window_state != "querying", self._window_state

        out: list[bytes | str] = []
        if self._window_state != "active":
            arg = [b"" if v is None else b"%d" % v for v in self._window_topbot]
            out.append(b"\x1b[%sr" % b";".join(arg))

            save_origin_mode = self._virt_terminal_mode.dec_modes[6]
            self._virt_terminal_mode.dec_modes[6] = b"l"
            out.extend(self._virt_terminal_mode.mode_chunks())
            self._virt_terminal_mode.dec_modes[6] = save_origin_mode

            w_row, col = self._virt_rowcol
            if w_row and col:
                t_row = self._terminal_row_from_window(w_row)
                out.append(b"\x1b[%d;%dH" % (t_row, col))  # CUP

            self._window_state = "active"

        self._virt_terminal_mode.add_chunk(chunk)

        if isinstance(chunk, bytes) and (rxm := OUT_CODE_RX.fullmatch(chunk)):
            code = rxm.lastgroup
            assert code, rxm.groupdict()
            # body = rxm[code]

            if code == "ris":
                self._virt_rowcol = (1, 1)
                self._virt_scroll_topbot = (None, None)
                out = [b"\x1b[2J"]  # no setup; clear screen; re-init after
                out.extend(self.checkpoint_state(time))  # trigger re-setup
            elif code == "cup":
                # TODO: modify coordinates
                pass
            elif code == "decom":
                pass  # recorded in _virt_terminal_mode, NOT passed through
            elif code in ("decsed", "ed"):
                out.extend(self.checkpoint_state(time))  # save cursor
                # TODO: emulate screen erase with line erase
            elif code == "decstbm":
                self._virt_rowcol = (1, 1)
                # TODO: set self._virt_scroll_topbot to new margins
                out = self.checkpoint_state(time)  # re-setup with new settings
            elif code == "decstr":
                self._virt_scroll_topbot = (None, None)
                out = self.checkpoint_state(time)  # re-setup with new settings
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
            self._virt_rowcol = (None, None)  # cannot predict, invalidate
            out.append(chunk)  # pass through

        return out

    def convert_input_chunk(self, chunk: bytes | str) -> list[bytes | str]:
        """Processes a chunk received from the terminal.
        - chunk: escape code or text received from terminal

        Returns a list of chunks (if any) to be handled by the caller:
        - locally generated cursor query replies are consumed
        - other cursor query replies translated to window coordinates
        - other chunks are returned as-is
        """
        if isinstance(chunk, bytes) and (rxm := IN_CODE_RX.fullmatch(chunk)):
            code = rxm.lastgroup
            if code == "cpr":
                t_row, col = [int(v) for v in rxm[code].split(b";")]
                w_row = self._virt_row_from_terminal(t_row)
                if pending := len(self._query_deadlines):
                    del self._query_deadlines[:1]
                    if self._window_state == "querying" and pending == 1:
                        self._window_state = "saved"
                        self._virt_rowcol = (w_row, col)
                        return []  # swallow response to our query
                return [b"\x1b[%d;%dR" % (w_row, col)]
            else:
                assert False, (code, rxm.groupdict())  # unknown named group?
        else:
            return [chunk]  # pass through

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
