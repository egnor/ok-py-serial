"""Unit tests for ok_serial._tracker.SerialTracker."""

import asyncio
import json
import threading
import time
import pytest

import ok_serial
from ok_serial._tracker import SerialTracker, TrackerOptions


@pytest.fixture
def scan_override(monkeypatch, tmp_path):
    """Fixture that controls what scan_serial_ports() returns via JSON file."""
    path = tmp_path / "scan.json"
    path.write_text("{}")
    monkeypatch.setenv("OK_SERIAL_SCAN_OVERRIDE", str(path))

    def set_ports(ports: dict[str, dict[str, str]]):
        path.write_text(json.dumps(ports))

    return set_ports


def test_connect_sync_finds_port(pty_serial, scan_override):
    scan_override({pty_serial.path: {"name": "test"}})
    with SerialTracker("name:test") as tracker:
        conn = tracker.connect_sync(timeout=1)
        assert conn is not None
        assert conn.port == pty_serial.path


def test_connect_sync_no_match_times_out(pty_serial, scan_override):
    scan_override({pty_serial.path: {"name": "other"}})
    with SerialTracker("name:nomatch") as tracker:
        conn = tracker.connect_sync(timeout=0.2)
        assert conn is None


def test_connect_sync_reuses_connection(pty_serial, scan_override):
    scan_override({pty_serial.path: {"name": "test"}})
    with SerialTracker("name:test") as tracker:
        conn1 = tracker.connect_sync(timeout=1)
        conn2 = tracker.connect_sync(timeout=1)
        assert conn1 is conn2


def test_connect_sync_reconnects_after_close(pty_serial, scan_override):
    scan_override({pty_serial.path: {"name": "test"}})
    with SerialTracker("name:test") as tracker:
        conn1 = tracker.connect_sync(timeout=1)
        conn1.close()
        conn2 = tracker.connect_sync(timeout=1)
        assert conn2 is not conn1
        assert conn2.port == pty_serial.path


def test_connect_sync_waits_for_port_to_appear(pty_serial, scan_override):
    scan_override({})  # No ports initially

    def add_port_later():
        time.sleep(0.15)
        scan_override({pty_serial.path: {"name": "delayed"}})

    thread = threading.Thread(target=add_port_later)
    thread.start()
    opts = TrackerOptions(
        matcher=ok_serial.SerialPortMatcher("name:delayed"),
        scan_interval=0.05,
    )
    with SerialTracker(opts) as tracker:
        conn = tracker.connect_sync(timeout=2)
        assert conn is not None
        assert conn.port == pty_serial.path
    thread.join()


async def test_connect_async_finds_port(pty_serial, scan_override):
    scan_override({pty_serial.path: {"name": "async_test"}})
    with SerialTracker("name:async_test") as tracker:
        conn = await asyncio.wait_for(tracker.connect_async(), timeout=1)
        assert conn.port == pty_serial.path


async def test_connect_async_waits_for_port(pty_serial, scan_override):
    scan_override({})

    async def add_port_later():
        await asyncio.sleep(0.1)
        scan_override({pty_serial.path: {"name": "appears"}})

    opts = TrackerOptions(
        matcher=ok_serial.SerialPortMatcher("name:appears"),
        scan_interval=0.05,
    )
    with SerialTracker(opts) as tracker:
        asyncio.create_task(add_port_later())
        conn = await asyncio.wait_for(tracker.connect_async(), timeout=2)
        assert conn.port == pty_serial.path
