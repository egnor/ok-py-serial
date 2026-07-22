import logging
import re
from typing import Literal

from ok_serial.terminal.mode_tracker import TerminalModeTracker

QUERY_PASSTHRU_TIMEOUT = 1.0  # seconds
QUERY_WARNING_TIMEOUT = 10.0  # warn if a cursor query takes this long
CURSOR_QUERY_RX = re.compile(b"(?:\x1b\\[|\x9b)6n")
CURSOR_REPLY_RX = re.compile(b"(?:\x1b\\[|\x9b)(\\d+);(\\d+)R")


class TerminalDecorator:
    """Modifies terminal output to show extra text around the cursor (for
    status messages, alerts, etc) without disrupting base rendering too much.
    Does not perform I/O directly, but processes chunks (per TerminalChunker)
    on their way to/from the terminal, via these properties:

    Input *queues* (caller should append, culled by .update() as processed):
    - .add_base (chunk list) - base terminal data from serial port
    - .add_above (chunk lists) - message lines to insert above the cursor and
        leave in place (eg. important status messages/logs)
    - .add_from_terminal (chunk list) - input chunks received from the terminal

    Input *values* (caller should set/update, .update() observes changes):
    - .set_right - message (chunk list) to show immediately after the cursor,
        moving with the cursor until removed or replaced
    - .set_below - message lines (chunk lists) to insert below the cursor,
        moving with the cursor until removed or replaced

    *Output* queues (appended by .update(), caller should cull once handled):
    - .out_to_terminal (chunk list) - to send directly to the terminal
    - .out_from_terminal (chunk list) - filtered terminal input to handle

    "Decorations" (.add_above/.set_below lines, .set_right) can include
    SGR-type directives (starting from reset) but must
    be a single line without cursor shenanigans. Auto-wrap is disabled for
    decorations, so they will be truncated at the right margin.

    Caveats: base rendering isn't disrupted "too much", but...
    - adding decorations above/below moves lines around and can change the row
    - adding and removing decorations to the right can erase existing content
    - decorations get disrupted if base content switches primary/alt screens
    - if the cursor is outside the scrolling margins, line display is glitchy
    """

    def __init__(self) -> None:
        self.add_base: list[bytes | str] = []
        self.add_above: list[list[bytes | str]] = []
        self.set_right: list[bytes | str] = []
        self.set_below: list[list[bytes | str]] = []
        self.out_to_terminal: list[bytes | str] = []

        self.add_from_terminal: list[bytes | str] = []
        self.out_from_terminal: list[bytes | str] = []

        # terminal mode tracking: the mode set by base content, the mode
        # to use for decorations, and what the terminal is actually doing
        self._base_mode = TerminalModeTracker()
        self._active_mode = self._base_mode

        # cursor tracking: the base cursor column, and cursor excursion status
        # (between cols, the cursor *row* remains aligned with the base cursor)
        self._base_col: int | Literal["unknown", "querying"] = 1
        self._cursor_pos: Literal["base", "roam"] = "base"
        self._query_warning_time: float = 0.0  # if "querying", when query sent
        self._query_passthru: list[float] = []  # expiration times

        # currently displayed right/below decorations for comparison
        # (above decorations are inserted and left in place forever)
        self._now_below: list[list[bytes | str]] = []
        self._now_right: list[bytes | str] = []

    def update(self, time: float) -> None:
        """Processes input properties and updates output properties.
        - time: clock time in seconds (with any consistent epoch)
        """

        # process input from terminal; match against pending passthru queries,
        # then our own. (note, this assumes no passthru once in "querying")
        for chunk in self.add_from_terminal:
            if isinstance(chunk, bytes) and (m := CURSOR_REPLY_RX.match(chunk)):
                if self._query_passthru:
                    del self._query_passthru[:1]
                elif self._base_col == "querying":
                    self._base_col = int(m.group(2))
                    continue  # we issued the query; consume the result
            self.out_from_terminal.append(chunk)
        self.add_from_terminal.clear()

        # expire pending passthru queries if we never saw a response
        while self._query_passthru and time > self._query_passthru[0]:
            del self._query_passthru[0]

        if self._base_col == "querying" and time > self._query_warning_time:
            logging.warning("Slow terminal query response (still waiting)")
            self._query_warning_time = time + QUERY_WARNING_TIMEOUT

        # strategize - trim decorations right/below of cursor if:
        # - base content is pending *and* reachable after trimming right/below
        # - OR right/below decoration content changed and needs updating
        if self.add_base and (
            isinstance(self._base_col, int)
            or (self._can_move_cursor_to_base() and not self._now_below)
        ):
            clear_right, keep_below = bool(self._now_right), 0
        else:
            clear_right, keep_below = (self.set_right != self._now_right), 0
            while (
                keep_below < len(self.set_below)
                and keep_below < len(self._now_below)
                and self.set_below[keep_below] == self._now_below[keep_below]
            ):
                keep_below += 1

        # clear right of cursor if requested and possible
        if clear_right and self._can_move_cursor_to_base():
            self._switch_terminal_mode(self._new_decoration_mode())
            self._move_cursor_to_base()
            self._emit(b"\x1b[K")  # caveat: leaves a hole right of cursor
            self._now_right.clear()

        # delete below decoration rows if requested
        if del_below := len(self._now_below) - keep_below:
            assert del_below > 0, (self._now_below, keep_below)
            self._switch_terminal_mode(self._new_decoration_mode())
            self._prepare_cursor_to_roam(time)  # deleting rows moves left
            self._emit(
                b"\x1b[%dB" % (keep_below + 1),  # move down
                b"\x1b[%dM" % del_below,  # delete rows
                b"\x1b[%dA" % (keep_below + 1),  # move back up
            )
            del self._now_below[-del_below:]

        # add base content if provided, reachable, and clear of decorations
        if self.add_base and (
            self._can_move_cursor_to_base()
            and not (self._now_right or self._now_below)
        ):
            self._move_cursor_to_base()
            self._switch_terminal_mode(self._base_mode)
            self._emit(*self.add_base)
            for chunk in self.add_base:
                # track queries from base content
                if isinstance(chunk, bytes) and CURSOR_QUERY_RX.match(chunk):
                    self._query_passthru.append(time + QUERY_PASSTHRU_TIMEOUT)
            self.add_base.clear()
            self._base_col = "unknown"  # can't predict ending point

        # add/replace right decoration if provided and reachable
        if self.set_right and (
            self._can_move_cursor_to_base() and not self._now_right
        ):
            self._switch_terminal_mode(self._new_decoration_mode())
            self._move_cursor_to_base()
            self._prepare_cursor_to_roam(time)  # adding content moves cursor
            self._emit(*self.set_right)
            self._now_right[:] = self.set_right

        # insert lines above if requested
        if self.add_above:
            self._switch_terminal_mode(self._new_decoration_mode())
            self._prepare_cursor_to_roam(time)  # adding content moves cursor
            self._emit(
                *[b"\n"] * len(self.add_above),  # scroll down to make room
                b"\x1b[%dA" % len(self.add_above),  # move back up
                b"\x1b[%dL" % len(self.add_above),  # insert rows
            )
            self._emit(b"\r", *self.add_above[0], b"\n")
            for next_line in self.add_above[1:]:
                self._switch_terminal_mode(self._new_decoration_mode())
                self._emit(b"\r", *next_line, b"\n")  # ends at base row
            self.add_above.clear()

        # insert lines below if requested
        assert len(self.set_below) >= len(self._now_below)
        if len(self.set_below) > len(self._now_below):
            skip_lines = len(self._now_below)
            assert self.set_below[:skip_lines] == self._now_below
            self._switch_terminal_mode(self._new_decoration_mode())
            self._prepare_cursor_to_roam(time)  # adding content moves cursor
            self._emit(*([b"\n"] * skip_lines))
            for next_line in self.set_below[skip_lines:]:
                self._switch_terminal_mode(self._new_decoration_mode())
                self._emit(b"\r", b"\n", *next_line)
                self._now_below.append(next_line[:])
            self._emit(b"\x1b[%dA" % len(self.set_below))  # ends at base row

        # for aesthetics, leave the cursor at base pos, if possible
        if self._can_move_cursor_to_base():
            self._move_cursor_to_base()

    def shutdown(self) -> None:
        """Performs a final update and adds cleanup to .out_to_terminal:
        - removes any right/below decorations
        - resets terminal mode to default state
        - moves to a new line if we're not already at one
        - clears the screen below the cursor
        """

        # consume pending input, erase lingering decorations, etc
        self.set_right.clear()
        self.set_below.clear()
        self.update(time=0.0)  # time doesn't matter here
        self._switch_terminal_mode(TerminalModeTracker())  # default state
        if (self._cursor_pos, self._base_col) != ("base", 1):
            self._emit(b"\r\n")  # move to new line if not already there
        self._emit(b"\x1b[J")  # clear from cursor to end of display

    def _can_move_cursor_to_base(self) -> bool:
        return self._cursor_pos == "base" or isinstance(self._base_col, int)

    def _move_cursor_to_base(self) -> None:
        assert self._can_move_cursor_to_base()
        if self._cursor_pos != "base":
            assert isinstance(self._base_col, int), self._base_col
            self.out_to_terminal.append(b"\x1b[%dG" % self._base_col)
            self._cursor_pos = "base"

    def _prepare_cursor_to_roam(self, time: float) -> None:
        if (self._cursor_pos, self._base_col) == ("base", "unknown"):
            self.out_to_terminal.append(b"\x1b[6n")
            self._base_col = "querying"
            self._query_warning_time = time + QUERY_WARNING_TIMEOUT
        self._cursor_pos = "roam"

    def _switch_terminal_mode(self, mode: TerminalModeTracker) -> None:
        if mode is not self._active_mode:
            mode_chunks = mode.mode_chunks(base=self._active_mode)
            self.out_to_terminal.extend(mode_chunks)
            self._active_mode = mode

    def _new_decoration_mode(self) -> TerminalModeTracker:
        mode = self._base_mode.copy()
        mode.add_chunk(b"\x0f")  # use G0
        mode.add_chunk(b"\x1b(B")  # G0 = US-ASCII
        mode.add_chunk(b'\x1b[0"q')  # character protection off
        mode.add_chunk(b"\x1b[m")  # reset SGR
        mode.add_chunk(b"\x1b[4l")  # reset IRM - no insert mode
        mode.add_chunk(b"\x1b[20l")  # reset LNM - normal newline mode
        mode.add_chunk(b"\x1b[?7l")  # reset DECAWM - do not wrap at EOL
        return mode

    def _emit(self, *chunks: bytes | str) -> None:
        self.out_to_terminal.extend(chunks)
        for chunk in chunks:
            self._active_mode.add_chunk(chunk)
