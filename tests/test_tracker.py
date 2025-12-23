"""Unit tests for ok_serial._tracker.ok_serial.SerialTracker."""

import asyncio
import threading
import time

import ok_serial


def test_connect_sync_finds_port(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    with ok_serial.SerialTracker("test") as tracker:
        conn = tracker.connect_sync(timeout=1)
        assert conn is not None
        assert conn.port_name == pty_serial.path


def test_connect_sync_no_match_times_out(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "other"}})
    with ok_serial.SerialTracker("nomatch") as tracker:
        conn = tracker.connect_sync(timeout=0.2)
        assert conn is None


def test_connect_sync_reuses_connection(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    with ok_serial.SerialTracker("test") as tracker:
        conn1 = tracker.connect_sync(timeout=1)
        conn2 = tracker.connect_sync(timeout=1)
        assert conn1 is conn2


def test_connect_sync_reconnects_after_close(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    with ok_serial.SerialTracker("test") as tracker:
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
    opts = ok_serial.TrackerOptions(scan_interval=0.05)
    with ok_serial.SerialTracker("delayed", topts=opts) as tracker:
        conn = tracker.connect_sync(timeout=2)
        assert conn is not None
        assert conn.port_name == pty_serial.path
    thread.join()


async def test_connect_async_finds_port(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "async_test"}})
    with ok_serial.SerialTracker("async_test") as tracker:
        conn = await asyncio.wait_for(tracker.connect_async(), timeout=1)
        assert conn.port_name == pty_serial.path


async def test_connect_async_waits_for_port(pty_serial, set_scan_override):
    set_scan_override({})

    async def add_port_later():
        await asyncio.sleep(0.1)
        set_scan_override({pty_serial.path: {"name": "appears"}})

    opts = ok_serial.TrackerOptions(scan_interval=0.05)
    with ok_serial.SerialTracker("appears", topts=opts) as tracker:
        asyncio.create_task(add_port_later())
        conn = await asyncio.wait_for(tracker.connect_async(), timeout=2)
        assert conn.port_name == pty_serial.path
