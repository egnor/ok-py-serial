"""Unit tests for ok_serial._connection."""

import asyncio
import termios
import threading
import time
import pytest

import ok_serial
from ok_serial import _connection
from ok_serial import _exceptions

#
# Basic smoke test
#


def test_basic_connection(pty_serial):
    with ok_serial.SerialConnection(pty_serial.path, baud=57600) as conn:
        tcattr = termios.tcgetattr(pty_serial.simulated.fileno())
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = tcattr
        assert ispeed == termios.B57600

        pty_serial.control.write(b"TO SERIAL")
        assert conn.read_sync(min=9, timeout=10) == b"TO SERIAL"

        conn.write(b"FROM SERIAL")
        conn.drain_sync()
        assert pty_serial.control.read(256) == b"FROM SERIAL"


#
# Async I/O tests
#


async def test_async_read_basic(pty_serial):
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        # Exact size read
        pty_serial.control.write(b"ASYNC TEST")
        data = await conn.read_async(min=10, max=10)
        assert data == b"ASYNC TEST"

        # Partial read (max larger than available)
        pty_serial.control.write(b"HELLO")
        data = await conn.read_async(min=5, max=100)
        assert data == b"HELLO"


async def test_async_read_min_zero(pty_serial):
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        data = await conn.read_async(min=0, max=100)
        assert data == b""


async def test_async_read_waits_for_min(pty_serial):
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:

        async def delayed_write():
            await asyncio.sleep(0.02)
            pty_serial.control.write(b"PART1")
            await asyncio.sleep(0.02)
            pty_serial.control.write(b"PART2")

        write_task = asyncio.create_task(delayed_write())
        data = await conn.read_async(min=10, max=100)
        assert data == b"PART1PART2"
        await write_task


async def test_async_drain(pty_serial):
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        # Basic drain to completion
        conn.write(b"DRAIN TEST")
        result = await conn.drain_async(max=0)
        assert result is True
        assert pty_serial.control.read(256) == b"DRAIN TEST"

        # Drain with max threshold
        conn.write(b"0123456789")
        result = await conn.drain_async(max=5)
        assert result is True
        await conn.drain_async(max=0)
        assert pty_serial.control.read(256) == b"0123456789"


async def test_async_read_and_write_concurrent(pty_serial):
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:

        async def reader():
            return await conn.read_async(min=5, max=100)

        async def writer():
            conn.write(b"WRITE")
            await conn.drain_async()
            return True

        pty_serial.control.write(b"HELLO")
        read_result, write_result = await asyncio.gather(reader(), writer())
        assert read_result == b"HELLO"
        assert write_result is True
        assert pty_serial.control.read(256) == b"WRITE"


#
# Empty input buffer / timeout
#


def test_read_sync_timeout_empty_buffer(pty_serial):
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        start = time.monotonic()
        data = conn.read_sync(min=1, timeout=0.1)
        elapsed = time.monotonic() - start
        assert data == b""
        assert 0.05 <= elapsed <= 0.5


@pytest.mark.parametrize("timeout", [0, -1])
def test_read_sync_zero_or_negative_timeout(pty_serial, timeout):
    """Test that zero or negative timeout returns immediately with no data."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        start = time.monotonic()
        data = conn.read_sync(min=1, timeout=timeout)
        elapsed = time.monotonic() - start
        assert data == b""
        assert elapsed < 0.1


def test_read_sync_partial_data_timeout(pty_serial):
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        pty_serial.control.write(b"ABC")
        # Wait for data using min=0 read (no sleep needed)
        conn.read_sync(min=0, max=0, timeout=0.1)

        # Now request more than available
        data = conn.read_sync(min=10, timeout=0.1)
        assert data == b""
        assert conn.incoming_size() == 3


#
# Output buffer behavior
#


def test_drain_sync_completes(pty_serial):
    """Test drain_sync completes when buffer empties."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        conn.write(b"SMALL")
        result = conn.drain_sync(max=0, timeout=5.0)
        assert result is True
        assert pty_serial.control.read(256) == b"SMALL"


def test_drain_sync_zero_timeout(pty_serial):
    """Test drain_sync with zero timeout returns immediately."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        conn.write(b"TEST DATA")
        result = conn.drain_sync(max=0, timeout=0)
        assert isinstance(result, bool)


def test_drain_sync_with_max_threshold(pty_serial):
    """Test drain_sync succeeds when buffer drops to max threshold."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        conn.write(b"0123456789")
        result = conn.drain_sync(max=5, timeout=5.0)
        assert result is True


def test_outgoing_size_after_drain(pty_serial):
    """Test outgoing_size is zero after drain completes."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        assert conn.outgoing_size() == 0
        conn.write(b"1234567890")
        conn.drain_sync(timeout=5.0)
        assert conn.outgoing_size() == 0


def test_incoming_size_tracks_buffer(pty_serial):
    """Test incoming_size tracks data correctly."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        assert conn.incoming_size() == 0
        pty_serial.control.write(b"12345")

        # Use read to wait for data to arrive (with min=0 to not block forever)
        while conn.incoming_size() < 5:
            conn.read_sync(min=0, max=0, timeout=0.1)

        assert conn.incoming_size() == 5
        conn.read_sync(min=2, max=2, timeout=1.0)
        assert conn.incoming_size() == 3


#
# min/max parameter edge cases
#


def test_read_sync_max_limits_output(pty_serial):
    """Test that max parameter limits the amount of data returned."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        pty_serial.control.write(b"ABCDEFGHIJ")

        # Wait for all 10 bytes to arrive
        while conn.incoming_size() < 10:
            conn.read_sync(min=0, max=0, timeout=0.1)

        # Now read with max=5 - should return exactly 5 bytes
        data = conn.read_sync(min=1, max=5, timeout=1.0)
        assert data == b"ABCDE"
        assert conn.incoming_size() == 5


def test_read_sync_min_equals_max(pty_serial):
    """Test reading exact number of bytes when min equals max."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        pty_serial.control.write(b"EXACTLY10!")
        data = conn.read_sync(min=10, max=10, timeout=1.0)
        assert data == b"EXACTLY10!"


def test_read_sync_min_zero_returns_available(pty_serial):
    """Test that min=0 returns whatever is available."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        pty_serial.control.write(b"AVAILABLE")
        # First wait for data to arrive
        while conn.incoming_size() < 9:
            conn.read_sync(min=0, max=0, timeout=0.1)
        data = conn.read_sync(min=0, max=100, timeout=0)
        assert data == b"AVAILABLE"


def test_read_sync_min_zero_empty_buffer(pty_serial):
    """Test that min=0 with empty buffer returns empty bytes."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        data = conn.read_sync(min=0, max=100, timeout=0)
        assert data == b""


#
# Connection close and exception handling
#


def test_operations_after_close_raise(pty_serial):
    """Test that read/write/drain after close raises SerialIoClosed."""
    conn = ok_serial.SerialConnection(pty_serial.path, baud=115200)
    conn.close()

    with pytest.raises(_exceptions.SerialIoClosed):
        conn.read_sync(min=1, timeout=1.0)

    with pytest.raises(_exceptions.SerialIoClosed):
        conn.write(b"test")

    with pytest.raises(_exceptions.SerialIoClosed):
        conn.drain_sync(timeout=1.0)


async def test_async_operations_after_close_raise(pty_serial):
    """Test that async read/drain after close raises SerialIoClosed."""
    conn = ok_serial.SerialConnection(pty_serial.path, baud=115200)
    conn.close()

    with pytest.raises(_exceptions.SerialIoClosed):
        await conn.read_async(min=1)

    with pytest.raises(_exceptions.SerialIoClosed):
        await conn.drain_async()


def test_context_manager_closes_on_exit(pty_serial):
    """Test that context manager properly closes connection."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        conn.write(b"test")
        conn.drain_sync(timeout=1.0)

    with pytest.raises(_exceptions.SerialIoClosed):
        conn.read_sync(min=1, timeout=0.1)


def test_multiple_close_is_safe(pty_serial):
    """Test that calling close multiple times is safe."""
    conn = ok_serial.SerialConnection(pty_serial.path, baud=115200)
    conn.close()
    conn.close()
    conn.close()


#
# Multi-threaded access tests
#


def test_concurrent_reads_and_writes(pty_serial):
    """Test concurrent read and write from multiple threads."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        results = {"read": None, "write": None}
        errors = []

        def reader():
            try:
                results["read"] = conn.read_sync(min=5, timeout=5.0)
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                conn.write(b"HELLO")
                conn.drain_sync(timeout=5.0)
                results["write"] = True
            except Exception as e:
                errors.append(e)

        pty_serial.control.write(b"WORLD")

        read_thread = threading.Thread(target=reader)
        write_thread = threading.Thread(target=writer)
        read_thread.start()
        write_thread.start()
        read_thread.join(timeout=10.0)
        write_thread.join(timeout=10.0)

        assert not errors, f"Errors occurred: {errors}"
        assert results["read"] == b"WORLD"
        assert results["write"] is True
        assert pty_serial.control.read(256) == b"HELLO"


#
# Large data transfer tests
#


def test_large_write_and_drain(pty_serial):
    """Test writing and draining a larger amount of data."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        data = b"X" * 1024
        conn.write(data)
        result = conn.drain_sync(timeout=10.0)
        assert result is True

        received = b""
        while len(received) < 1024:
            chunk = pty_serial.control.read(4096)
            if not chunk:
                break
            received += chunk
        assert received == data


async def test_large_async_read(pty_serial):
    """Test async reading of larger amounts of data."""
    with ok_serial.SerialConnection(pty_serial.path, baud=115200) as conn:
        data = b"Y" * 512
        pty_serial.control.write(data)

        received = b""
        while len(received) < 512:
            chunk = await conn.read_async(min=1, max=512 - len(received))
            received += chunk
        assert received == data


#
# Utility function tests
#


def test_deadline_from_timeout(mocker):
    TMAX = threading.TIMEOUT_MAX
    mocker.patch("time.monotonic")
    time.monotonic.return_value = 1000.0

    assert _connection._deadline_from_timeout(-1) == 0
    assert _connection._deadline_from_timeout(0) == 0
    assert _connection._deadline_from_timeout(1) == 1001.0
    assert _connection._deadline_from_timeout(None) == TMAX
    assert _connection._deadline_from_timeout(TMAX - 1) == TMAX
    assert _connection._deadline_from_timeout(TMAX) == TMAX
    assert _connection._deadline_from_timeout(TMAX + 1) == TMAX


def test_timeout_from_deadline(mocker):
    TMAX = threading.TIMEOUT_MAX
    mocker.patch("time.monotonic")
    time.monotonic.return_value = 1000.0

    assert _connection._timeout_from_deadline(-1) == 0
    assert _connection._timeout_from_deadline(0) == 0
    assert _connection._timeout_from_deadline(999) == 0
    assert _connection._timeout_from_deadline(1000) == 0
    assert _connection._timeout_from_deadline(1001) == 1
    assert _connection._timeout_from_deadline(TMAX - 1) == TMAX - 1001
    assert _connection._timeout_from_deadline(TMAX) == TMAX
    assert _connection._timeout_from_deadline(TMAX + 1) == TMAX
