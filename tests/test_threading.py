"""Regression tests for the thread-safety claims in the README.

True concurrency bugs are hard to pin down with tests, so these mostly check
the *invariants* the fixes established, rather than trying to lose specific
races. See test_connection.py / test_monitor.py for functional coverage.
"""

import asyncio
import errno
import threading
import time
import traceback

import pytest

import ok_serial

#
# A dead connection reports itself with a fresh exception every time, rather
# than re-raising one shared object (whose traceback would grow per raise,
# and which other threads hold at the same time)
#


def test_each_report_is_a_fresh_exception(pty_serial):
    conn = ok_serial.SerialConnection(port=pty_serial.path)
    conn.close()

    seen = []
    for _ in range(100):
        with pytest.raises(ok_serial.SerialIoClosed) as caught:
            conn.write(b"x")
        seen.append(caught.value)

    assert len(set(id(exc) for exc in seen)) == len(seen)  # all distinct
    assert all(str(exc) == str(seen[0]) for exc in seen)  # identical report
    assert all(exc.port == conn.port_name for exc in seen)

    # each traceback is the caller's, and the same size call after call
    depths = set(len(traceback.extract_tb(exc.__traceback__)) for exc in seen)
    assert len(depths) == 1, depths
    # ...and it names the caller, not the I/O thread that noticed the failure
    assert traceback.extract_tb(seen[0].__traceback__)[0].name == (
        "test_each_report_is_a_fresh_exception"
    )


def test_close_after_failure_reports_both(pty_serial):
    conn = ok_serial.SerialConnection(port=pty_serial.path)
    cause = OSError(errno.EIO, "simulated")
    with conn._io.monitor:
        exc_type = ok_serial.SerialIoException
        conn._io.poison_locked(exc_type, "read exploded", cause)
    conn.close()  # closure supersedes, but must not hide the original

    with pytest.raises(ok_serial.SerialIoClosed) as caught:
        conn.write(b"x")
    assert "closed" in str(caught.value)
    assert "read exploded" in str(caught.value)
    assert caught.value.__cause__ is cause


@pytest.mark.parametrize("method", ["set_signals", "get_signals"])
def test_signal_failure_wakes_blocked_waiters(pty_serial, monkeypatch, method):
    """Whatever poisons a connection must also interrupt threads already
    waiting on it, per "any error or closure on a connection interrupts all
    operations on that connection"."""

    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        result = []

        def reader():
            try:
                result.append(conn.read_sync(timeout=None))
            except ok_serial.SerialIoException as ex:
                result.append(ex)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        time.sleep(0.2)  # let the reader block in monitor.wait()

        # make the control lines fail with something that poisons (not ENOTTY)
        def boom(*args):
            raise OSError(errno.EIO, "simulated control line failure")

        monkeypatch.setattr(type(conn.pyserial), "dtr", property(boom, boom))
        with pytest.raises(ok_serial.SerialIoException):
            if method == "set_signals":
                conn.set_signals(dtr=True)
            else:
                conn.get_signals()

        thread.join(timeout=10)
        assert not thread.is_alive()  # the blocked reader must have woken
        assert isinstance(result[0], ok_serial.SerialIoException)


#
# Teardown must not interleave with itself
#


def test_concurrent_close_is_safe(pty_serial):
    conn = ok_serial.SerialConnection(port=pty_serial.path)

    start = threading.Barrier(5)
    errors = []

    def closer():
        start.wait()
        try:
            conn.close()
        except Exception:
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=closer) for _ in range(4)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    for thread in conn._io._threads:
        assert not thread.is_alive()  # every I/O thread was joined


def test_close_during_with_block_is_safe(pty_serial):
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        conn.close()  # the `with` exit will close it a second time


#
# Async waiters belong to whichever loop awaits them, not to whichever loop
# (if any) happened to be running at construction time
#


def test_read_async_without_loop_at_construction(pty_serial):
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:

        async def read():
            pty_serial.control.write(b"HELLO")
            return await asyncio.wait_for(conn.read_async(), 10)

        assert asyncio.run(read()) == b"HELLO"


def test_read_async_from_two_loops_in_two_threads(pty_serial):
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        ready = threading.Barrier(3)
        got = []

        def reader():
            async def read():
                task = asyncio.ensure_future(conn.read_async())
                await asyncio.sleep(0)  # let the waiter register
                ready.wait()
                got.append(await asyncio.wait_for(task, 10))

            asyncio.run(read())

        threads = [threading.Thread(target=reader) for _ in range(2)]
        for thread in threads:
            thread.start()
        ready.wait()

        # Each read takes everything buffered, so feed the loops one at a
        # time; either loop may win the first one, so wait on the count.
        pty_serial.control.write(b"ONE")
        deadline = time.monotonic() + 10
        while len(got) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(got) == 1

        pty_serial.control.write(b"TWO")
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()

        assert sorted(got) == [b"ONE", b"TWO"]


def test_close_after_event_loop_closed(pty_serial):
    holder = {}

    async def setup():
        conn = ok_serial.SerialConnection(port=pty_serial.path)
        holder["conn"] = conn
        task = asyncio.ensure_future(conn.read_async())
        await asyncio.sleep(0)  # let the waiter register
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(setup())  # loop is closed, connection is still open
    conn = holder["conn"]

    # the reader thread must survive the loop going away under it
    pty_serial.control.write(b"AFTER")
    assert conn.read_sync(timeout=10) == b"AFTER"
    conn.close()  # must not raise RuntimeError("Event loop is closed")


#
# Monitor bookkeeping
#


def test_concurrent_connect_sync(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    mopts = ok_serial.SerialMonitorOptions(scan_interval=0)
    with ok_serial.SerialConnectionMonitor("test", mopts=mopts) as monitor:
        conns, errors = [], []

        def connect():
            try:
                conns.append(monitor.connect_sync(timeout=10))
            except Exception:
                errors.append(traceback.format_exc())

        threads = [threading.Thread(target=connect) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert len(conns) == 8
        assert len(set(id(c) for c in conns)) == 1  # all share one connection


def test_exit_releases_the_port_for_a_new_monitor(
    pty_serial, set_scan_override
):
    set_scan_override({pty_serial.path: {"name": "test"}})
    with ok_serial.SerialConnectionMonitor("test") as monitor:
        first = monitor.connect_sync(timeout=10)
        assert first is not None

    # Leaving the `with` block must release the port and its locks -- a
    # same-process reopen is refused while any are held -- so making a new
    # monitor is all it takes to pick the port back up.
    with ok_serial.SerialConnectionMonitor("test") as monitor:
        second = monitor.connect_sync(timeout=10)
        assert second is not None
        assert second is not first


#
# fileno() passthrough
#


def test_fileno(pty_serial):
    conn = ok_serial.SerialConnection(port=pty_serial.path)
    assert conn.fileno() >= 0
    conn.close()
    assert conn.fileno() == -1
