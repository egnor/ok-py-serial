import re
from typing import Literal

from ok_serial._terminal_mode_tracker import TerminalModeTracker

CURSOR_QUERY_TIMEOUT = 1.0  # seconds

CURSOR_QUERY_RX = re.compile(b"(?:\x1b\\[|\x9b)6n")

CURSOR_REPLY_RX = re.compile(b"(?:\x1b\\[|\x9b)(\\d+);(\\d+)R")


class TerminalDecorator:
    """Modifies terminal output to show extra text around the cursor (for
    status messages, alerts, etc) without disrupting base output rendering.
    Does not perform I/O directly, but processes chunks (per TerminalChunker)
    on their way to/from the terminal, via these properties:

    Input *queues* (caller should append, culled by .update() as processed):
    - .add_base (chunk list) - base terminal data from serial port
    - .add_above (chunk lists) - message lines to insert above the cursor and
        leave in place (eg. important status messages/logs)
    - .add_input (chunk list) - input chunks received from the terminal

    Input *values* (caller should set/update, .update() observes changes):
    - .set_right - message (chunk list) to show immediately after the cursor,
        moving with the cursor until removed or replaced
    - .set_below - message lines (chunk lists) to insert below the cursor,
        moving with the cursor until removed or replaced

    *Output* queues (appended by .update(), caller should cull once handled):
    - .out_to_terminal (chunk list) - to send directly to the terminal
    - .out_input (chunk list) - filtered terminal input to handle

    "Decorations" (.add_above/.set_below lines, .set_right) can include
    SGR-type directives (starting from reset) but must
    be a single line without cursor shenanigans. Auto-wrap is disabled for
    decorations so they will be truncated at the right margin.
    """

    def __init__(self) -> None:
        self.add_base: list[bytes | str] = []
        self.add_above: list[list[bytes | str]] = []
        self.set_right: list[bytes | str] = []
        self.set_below: list[list[bytes | str]] = []
        self.out_to_terminal: list[bytes | str] = []

        self.add_input: list[bytes | str] = []
        self.out_input: list[bytes | str] = []

        # terminal mode tracking: the mode set by base content, the mode
        # to use for decorations, and what the terminal is actually doing
        self._base_mode = TerminalModeTracker()
        self._decoration_mode = TerminalModeTracker()
        self._active_mode = self._base_mode

        # cursor tracking: the base cursor column, and cursor excursion status
        # (between cols, the cursor *row* remains aligned with the base cursor)
        self._base_col: int | Literal["unknown", "querying"] = "unknown"
        self._cursor_pos: Literal["base", "roam"] = "base"
        self._query_deadlines: list[float] = []  # cursor query timeouts

        # currently displayed right/below decorations for comparison
        # (above decorations are inserted and left in place forever)
        self._now_below: list[list[bytes | str]] = []
        self._now_right: list[bytes | str] = []

        # disable linewrap for decorations to avoid cursor tracking problems
        self._decoration_mode.add_chunk(b"\x1b[?7l")

    def update(self, time: float) -> None:
        """Processes input properties and updates output properties.
        - time: clock time in seconds (with any consistent epoch)
        """

        # match cursor position replies to queries, including ours
        # note, this assumes no additional queries issued in "querying" state
        for chunk in self.add_input:
            if isinstance(chunk, bytes) and (m := CURSOR_REPLY_RX.match(chunk)):
                del self._query_deadlines[:1]
                if self._base_col == "querying" and not self._query_deadlines:
                    self._base_col = int(m.group(2))
        self.add_input.clear()

        # time out expired queries if the response failed somehow
        while self._query_deadlines and time > self._query_deadlines[0]:
            del self._query_deadlines[0]
        if self._base_col == "querying" and not self._query_deadlines:
            self._base_col = "unknown"

        # strategize - trim decorations right/below of cursor if:
        # - base content is pending *and* reachable / ready to update
        # - OR right/below decoration content has changed
        if self.add_base and (
            isinstance(self._base_col, int)
            or (self._cursor_pos == "base" and self._can_return_to_base())
        ):
            clear_right = bool(self._now_right)
            keep_below = 0
        else:
            clear_right = self.set_right != self._now_right
            min_len = min(len(self.set_below), len(self._now_below))
            for keep_below in range(min_len):
                if self.set_below[keep_below] != self._now_below[keep_below]:
                    break

        # clear right of cursor if requested and possible
        if clear_right and self._can_return_to_base():
            self._switch_mode(self._decoration_mode)
            self._return_to_base()
            self._emit(b"\x1b[K")
            self._now_right.clear()

        # delete below decoration rows if requested
        if del_below := len(self._now_below) - keep_below:
            assert del_below > 0, (self._now_below, keep_below)
            self._switch_mode(self._decoration_mode)
            self._prepare_to_roam(time)  # deleting rows moves left
            self._emit(
                b"\x1b[%dB" % (keep_below + 1),  # move down
                b"\x1b[%dM" % del_below,  # delete rows
                b"\x1b[%dA" % (keep_below + 1),  # move back up
            )
            del self._now_below[-del_below:]

        # add base content if provided, reachable, and clear of decorations
        if self.add_base and (
            self._can_return_to_base()
            and not (self._now_right or self._now_below)
        ):
            self._return_to_base()
            self._switch_mode(self._base_mode)
            self._emit(*self.add_base)
            for chunk in self.add_base:
                # track queries from base content
                if isinstance(chunk, bytes) and CURSOR_QUERY_RX.match(chunk):
                    self._query_deadlines.append(time + CURSOR_QUERY_TIMEOUT)
            self.add_base.clear()
            self._base_col = "unknown"  # can't predict ending point

        # add/replace right decoration if provided and reachable
        if self.set_right and (
            self._can_return_to_base() and not self._now_right
        ):
            self._switch_mode(self._decoration_mode.copy())
            self._return_to_base()
            self._prepare_to_roam(time)  # adding content moves cursor
            self._emit(*self.set_right)
            self._now_right[:] = self.set_right

        # insert lines above if requested
        if self.add_above:
            self._switch_mode(self._decoration_mode)
            self._prepare_to_roam(time)  # adding content moves cursor
            self._emit(
                *[b"\n"] * len(self.add_above),  # scroll down to make room
                b"\x1b[%dA" % len(self.add_above),  # move back up
                b"\x1b[%dL" % len(self.add_above),  # insert rows
            )
            for above_line in self.add_above:
                self._switch_mode(self._decoration_mode.copy())  # start fresh
                self._emit(b"\r", *above_line, b"\n")  # ends at base row
            self.add_above.clear()

        # insert lines below if requested
        assert len(self.set_below) >= len(self._now_below)
        if len(self.set_below) > len(self._now_below):
            skip_lines = len(self._now_below)
            assert self.set_below[:skip_lines] == self._now_below
            self._switch_mode(self._decoration_mode)
            self._prepare_to_roam(time)  # adding content moves cursor
            self._emit(*([b"\n"] * skip_lines))
            for below_line in self.set_below[skip_lines:]:
                self._switch_mode(self._decoration_mode.copy())
                self._emit(b"\r", b"\n", *below_line)
            self._emit(b"\x1b[%dA" % len(self.set_below))  # ends at base row

        # for aesthetics, leave the cursor at base pos, if possible
        if self._can_return_to_base():
            self._return_to_base()

    def _prepare_to_roam(self, time: float) -> None:
        if (self._cursor_pos, self._base_col) == ("base", "unknown"):
            self.out_to_terminal.append(b"\x1b[6n")
            self._query_deadlines.append(time + CURSOR_QUERY_TIMEOUT)
            self._base_col = "querying"
        self._cursor_pos = "roam"

    def _can_return_to_base(self) -> bool:
        return self._cursor_pos == "base" or isinstance(self._base_col, int)

    def _return_to_base(self) -> None:
        assert self._can_return_to_base(), (self._cursor_pos, self._base_col)
        if self._cursor_pos != "base":
            assert isinstance(self._base_col, int), self._base_col
            self.out_to_terminal.append(b"\x1b[%dG" % self._base_col)
            self._cursor_pos = "base"

    def _switch_mode(self, mode: TerminalModeTracker):
        if mode is not self._active_mode:
            mode_chunks = mode.mode_chunks(base=self._active_mode)
            self.out_to_terminal.extend(mode_chunks)
            self._active_mode = mode

    def _emit(self, *chunks: bytes | str) -> None:
        self.out_to_terminal.extend(chunks)
        for chunk in chunks:
            self._active_mode.add_chunk(chunk)
