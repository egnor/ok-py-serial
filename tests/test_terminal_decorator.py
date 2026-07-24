from ok_serial.terminal.decorator import TerminalDecorator

# the decoration mode differs from the default base mode only by DECAWM,
# so mode switches show up in the output as these two escapes
WRAP_OFF = b"\x1b[?7l"
WRAP_ON = b"\x1b[?7h"


def to_term(deco: TerminalDecorator, time: float = 0.0) -> list[bytes | str]:
    """Runs update() and drains .out_to_terminal."""
    deco.update(time)
    chunks = deco.out_to_terminal[:]
    deco.out_to_terminal.clear()
    return chunks


def from_term(deco: TerminalDecorator) -> list[bytes | str]:
    """Drains .out_from_terminal (after some update())."""
    chunks = deco.out_from_terminal[:]
    deco.out_from_terminal.clear()
    return chunks


def test_idle_update_emits_nothing():
    deco = TerminalDecorator()
    assert to_term(deco) == []
    assert from_term(deco) == []


def test_base_content_passes_through():
    deco = TerminalDecorator()
    deco.add_base += ["hello", b"\r\n", "world"]
    assert to_term(deco) == ["hello", b"\r\n", "world"]
    assert deco.add_base == []  # input queue is culled once processed


def test_terminal_input_passes_through():
    deco = TerminalDecorator()
    deco.add_from_terminal += ["abc", b"\x1b[A", b"\x03"]
    deco.update(0.0)
    assert from_term(deco) == ["abc", b"\x1b[A", b"\x03"]
    assert deco.add_from_terminal == []


def test_unsolicited_cursor_reply_passes_through():
    # nobody asked for the cursor position, so a reply isn't ours to eat
    deco = TerminalDecorator()
    deco.add_from_terminal += [b"\x1b[3;7R"]
    deco.update(0.0)
    assert from_term(deco) == [b"\x1b[3;7R"]


def test_above_decoration_inserts_lines():
    # with the base cursor column known (fresh = col 1), no query is needed
    deco = TerminalDecorator()
    deco.add_above += [["one"], ["two"]]
    assert to_term(deco) == [
        WRAP_OFF,  # switch to decoration mode
        b"\n",
        b"\n",  # scroll down to make room
        b"\x1b[2A",  # move back up
        b"\x1b[2L",  # insert rows
        b"\r",
        "one",
        b"\n",
        b"\r",
        "two",
        b"\n",  # ends at the base row
        b"\x1b[1G",  # return the cursor to the base column
    ]
    assert deco.add_above == []


def test_below_decoration_inserts_and_truncates():
    deco = TerminalDecorator()
    deco.set_below = [["aaa"], ["bbb"]]
    assert to_term(deco) == [
        WRAP_OFF,  # decoration mode
        b"\r",
        b"\n",
        "aaa",
        b"\r",
        b"\n",
        "bbb",
        b"\x1b[2A",  # back up to the base row
        b"\x1b[1G",
    ]
    assert to_term(deco) == []  # unchanged value => no further output

    deco.set_below = [["aaa"]]  # drop the second line, keeping the first
    assert to_term(deco) == [
        b"\x1b[2B",  # move below the kept line
        b"\x1b[1M",  # delete the stale row
        b"\x1b[2A",  # move back up
        b"\x1b[1G",
    ]


def test_right_decoration_set_replace_remove():
    deco = TerminalDecorator()
    deco.set_right = ["<A>"]
    assert to_term(deco) == [WRAP_OFF, b"\x1b[K", "<A>", b"\x1b[1G"]
    assert to_term(deco) == []  # unchanged value => no further output

    deco.set_right = ["<B>"]  # replaced: clear, then redraw
    assert to_term(deco) == [b"\x1b[K", "<B>", b"\x1b[1G"]

    deco.set_right = []  # removed: just clear
    assert to_term(deco) == [b"\x1b[K"]


def test_decoration_queries_cursor_after_base_content():
    # base output makes the cursor column unpredictable, so the first
    # decoration must ask the terminal where the cursor ended up
    deco = TerminalDecorator()
    deco.add_base += ["hello"]
    assert to_term(deco) == ["hello"]

    deco.set_right = ["<hi>"]
    assert to_term(deco) == [WRAP_OFF, b"\x1b[K", b"\x1b[6n", "<hi>"]

    # the reply is consumed (not forwarded) and enables cursor return
    deco.add_from_terminal += [b"\x1b[12;6R"]
    assert to_term(deco) == [b"\x1b[6G"]
    assert from_term(deco) == []


def test_base_content_deferred_until_query_answered():
    deco = TerminalDecorator()
    deco.add_base += ["hello"]
    to_term(deco)
    deco.set_below = [["status"]]
    assert to_term(deco) == [
        WRAP_OFF,
        b"\x1b[6n",
        b"\r",
        b"\n",
        "status",
        b"\x1b[1A",
    ]

    # more base content can't be placed until the query resolves
    deco.add_base += ["world"]
    assert to_term(deco, time=0.1) == []
    assert deco.add_base == ["world"]

    # the reply arrives: clear the decoration, add base, redecorate
    deco.add_from_terminal += [b"\x1b[9;6R"]
    assert to_term(deco, time=0.2) == [
        b"\x1b[1B",
        b"\x1b[1M",
        b"\x1b[1A",  # delete the below decoration
        b"\x1b[6G",  # return to the reported base column
        WRAP_ON,  # back to base mode
        "world",
        WRAP_OFF,  # decoration mode again
        b"\x1b[6n",  # new base content => new query
        b"\r",
        b"\n",
        "status",  # below decoration redrawn
        b"\x1b[1A",
    ]


def test_base_style_restored_after_decoration():
    # SGR state set by base content is reset for decorations and restored
    deco = TerminalDecorator()
    deco.add_base += [b"\x1b[1m", "bold"]
    assert to_term(deco) == [b"\x1b[1m", "bold"]

    deco.add_above += [["note"]]
    assert to_term(deco) == [
        b"\x1b[m",  # reset the base's bold for the decoration
        WRAP_OFF,
        b"\x1b[6n",
        b"\n",
        b"\x1b[1A",
        b"\x1b[1L",
        b"\r",
        "note",
        b"\n",
    ]

    deco.add_from_terminal += [b"\x1b[2;5R"]
    deco.add_base += ["more"]
    assert to_term(deco) == [
        b"\x1b[5G",
        b"\x1b[;1m",  # bold restored for base content
        WRAP_ON,
        "more",
    ]


def test_decoration_style_reset_for_base():
    # SGR used within a decoration doesn't leak into base content
    deco = TerminalDecorator()
    deco.add_above += [[b"\x1b[31m", "red note"]]
    assert to_term(deco) == [
        WRAP_OFF,
        b"\n",
        b"\x1b[1A",
        b"\x1b[1L",
        b"\r",
        b"\x1b[31m",
        "red note",
        b"\n",
        b"\x1b[1G",
    ]

    deco.add_base += ["plain"]
    assert to_term(deco) == [b"\x1b[m", WRAP_ON, "plain"]


def test_base_cursor_query_passed_through():
    # a query issued by base content gets its reply forwarded, not eaten
    deco = TerminalDecorator()
    deco.add_base += [b"\x1b[6n"]
    assert to_term(deco) == [b"\x1b[6n"]

    deco.add_from_terminal += [b"\x1b[4;2R"]
    deco.update(0.1)
    assert from_term(deco) == [b"\x1b[4;2R"]


def test_base_cursor_query_passthru_expires():
    deco = TerminalDecorator()
    deco.add_base += [b"\x1b[6n"]
    to_term(deco)  # registers the passthru at t=0

    deco.set_right = ["<hi>"]  # issues our own query
    to_term(deco)

    deco.update(2.0)  # past QUERY_PASSTHRU_TIMEOUT: passthru expires
    deco.add_from_terminal += [b"\x1b[8;3R"]
    assert to_term(deco, time=2.1) == [b"\x1b[3G"]  # reply was ours
    assert from_term(deco) == []


def test_reset_fresh():
    deco = TerminalDecorator()
    deco.reset()
    # at a known column 1 with default modes: reset the scrolling margins
    # (preserving the cursor) and clear below the cursor
    assert deco.out_to_terminal == [b"\x1b7", b"\x1b[r", b"\x1b8", b"\x1b[J"]


def test_reset_after_base_content():
    deco = TerminalDecorator()
    deco.add_base += ["hello"]
    to_term(deco)
    deco.reset()
    # ending column unknown, so move to a fresh line before clearing
    expect = [b"\x1b7", b"\x1b[r", b"\x1b8", b"\r", b"\n", b"\x1b[J"]
    assert deco.out_to_terminal == expect


def test_reset_reverts_keyboard_protocols():
    # regression: base content enabling modifyOtherKeys (which once crashed
    # the tracker) and kitty keyboard flags must be undone by reset()
    deco = TerminalDecorator()
    deco.add_base += [b"\x1b[>4;2m", b"\x1b[>1u", "hello"]
    to_term(deco)
    deco.reset()
    assert b"\x1b[>4m" in deco.out_to_terminal  # modifyOtherKeys off
    assert b"\x1b[<1u" in deco.out_to_terminal  # kitty flags popped
