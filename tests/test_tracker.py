"""Unit tests for ok_serial._tracker.ok_serial.SerialPortTracker."""

import asyncio
import threading
import time

import pytest

import ok_serial


def test_connect_sync_finds_port(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    with ok_serial.SerialPortTracker("test") as tracker:
        conn = tracker.connect_sync(timeout=1)
        assert conn is not None
        assert conn.port_name == pty_serial.path


def test_connect_sync_no_match_times_out(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "other"}})
    with ok_serial.SerialPortTracker("nomatch") as tracker:
        conn = tracker.connect_sync(timeout=0.2)
        assert conn is None


def test_connect_sync_reuses_connection(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    with ok_serial.SerialPortTracker("test") as tracker:
        conn1 = tracker.connect_sync(timeout=1)
        conn2 = tracker.connect_sync(timeout=1)
        assert conn1 is conn2


def test_connect_sync_reconnects_after_close(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    with ok_serial.SerialPortTracker("test") as tracker:
        conn1 = tracker.connect_sync(timeout=1)
        conn1.close()
        conn2 = tracker.connect_sync(timeout=1)
        assert conn2 is not conn1
        assert conn2.port_name == pty_serial.path


def test_connect_sync_waits_for_port_to_appear(pty_serial, set_scan_override):
    set_scan_override({})  # No ports initially

    def add_port_later():
        time.sleep(0.15)
        set_scan_override({pty_serial.path: {"name": "delayed"}})

    thread = threading.Thread(target=add_port_later)
    thread.start()
    opts = ok_serial.SerialTrackerOptions(scan_interval=0.05)
    with ok_serial.SerialPortTracker("delayed", topts=opts) as tracker:
        conn = tracker.connect_sync(timeout=2)
        assert conn is not None
        assert conn.port_name == pty_serial.path
    thread.join()


def test_scan_timeout_raises_exhausted(set_scan_override):
    set_scan_override({})  # No matching port will ever appear.
    opts = ok_serial.SerialTrackerOptions(scan_interval=0.05, scan_timeout=0.2)
    with ok_serial.SerialPortTracker("never", topts=opts) as tracker:
        with pytest.raises(ok_serial.SerialTrackerExhausted):
            tracker.connect_sync(timeout=2)


def test_scan_timeout_not_hit_when_port_present(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "prompt"}})
    opts = ok_serial.SerialTrackerOptions(scan_interval=0.05, scan_timeout=0.2)
    with ok_serial.SerialPortTracker("prompt", topts=opts) as tracker:
        conn = tracker.connect_sync(timeout=1)
        assert conn is not None
        assert conn.port_name == pty_serial.path


def test_reconnect_limit_zero_raises_on_disconnect(
    pty_serial, set_scan_override
):
    set_scan_override({pty_serial.path: {"name": "test"}})
    opts = ok_serial.SerialTrackerOptions(reconnect_limit=0)
    with ok_serial.SerialPortTracker("test", topts=opts) as tracker:
        conn = tracker.connect_sync(timeout=1)
        assert conn is not None
        conn.close()  # Forces a reconnect attempt on the next call.
        with pytest.raises(ok_serial.SerialTrackerExhausted):
            tracker.connect_sync(timeout=1)


def test_reconnect_limit_allows_then_exhausts(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    opts = ok_serial.SerialTrackerOptions(reconnect_limit=1)
    with ok_serial.SerialPortTracker("test", topts=opts) as tracker:
        conn1 = tracker.connect_sync(timeout=1)
        assert conn1 is not None
        conn1.close()
        conn2 = tracker.connect_sync(timeout=1)  # Reconnect #1, allowed.
        assert conn2 is not None
        assert conn2 is not conn1
        conn2.close()
        with pytest.raises(ok_serial.SerialTrackerExhausted):
            tracker.connect_sync(timeout=1)  # Reconnect #2, over the limit.


def test_multiple_matches_does_not_connect(pty_serial, set_scan_override):
    override = {pty_serial.path: {"name": "dup"}, "FAKE": {"name": "dup"}}
    set_scan_override(override)
    opts = ok_serial.SerialTrackerOptions(scan_interval=0.05)
    with ok_serial.SerialPortTracker("dup", topts=opts) as tracker:
        # Ambiguous match: refuse to pick a port, time out with no connection.
        assert tracker.connect_sync(timeout=0.3) is None


def test_multiple_matches_resolved_then_connects(pty_serial, set_scan_override):
    override = {pty_serial.path: {"name": "dup"}, "FAKE": {"name": "dup"}}
    set_scan_override(override)
    opts = ok_serial.SerialTrackerOptions(scan_interval=0.05)
    with ok_serial.SerialPortTracker("dup", topts=opts) as tracker:
        assert tracker.connect_sync(timeout=0.2) is None
        set_scan_override({pty_serial.path: {"name": "dup"}})  # Disambiguate.
        conn = tracker.connect_sync(timeout=1)
        assert conn is not None
        assert conn.port_name == pty_serial.path


async def test_connect_async_finds_port(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "async_test"}})
    with ok_serial.SerialPortTracker("async_test") as tracker:
        conn = await asyncio.wait_for(tracker.connect_async(), timeout=1)
        assert conn.port_name == pty_serial.path


async def test_connect_async_waits_for_port(pty_serial, set_scan_override):
    set_scan_override({})

    async def add_port_later():
        await asyncio.sleep(0.1)
        set_scan_override({pty_serial.path: {"name": "appears"}})

    opts = ok_serial.SerialTrackerOptions(scan_interval=0.05)
    with ok_serial.SerialPortTracker("appears", topts=opts) as tracker:
        asyncio.create_task(add_port_later())
        conn = await asyncio.wait_for(tracker.connect_async(), timeout=2)
        assert conn.port_name == pty_serial.path
