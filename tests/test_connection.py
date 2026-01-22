"""Unit tests for ok_serial.SerialConnection."""

import asyncio
import termios
import threading
import time
import pytest

import ok_serial
import serial

#
# Basic connectivity test
#


def test_basic_connection(pty_serial):
    with ok_serial.SerialConnection(port=pty_serial.path, baud=57600) as conn:
        tcattr = termios.tcgetattr(pty_serial.simulated.fileno())
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = tcattr
        assert ispeed == termios.B57600

        pty_serial.control.write(b"TO SERIAL")
        assert conn.read_sync(timeout=10) == b"TO SERIAL"

        conn.write(b"FROM SERIAL")
        conn.drain_sync()
        assert pty_serial.control.read(256) == b"FROM SERIAL"


def test_connection_with_port_match(pty_serial, set_scan_override):
    set_scan_override({pty_serial.path: {"name": "test"}})
    with ok_serial.SerialConnection(match="test") as conn:
        assert conn.port_name == pty_serial.path

    with pytest.raises(ok_serial.SerialOpenException):
        with ok_serial.SerialConnection(match="toast") as conn:
            pass


#
# Async I/O tests
#


async def test_async_read_basic(pty_serial):
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        # Exact size read
        pty_serial.control.write(b"ASYNC TEST")
        data = await conn.read_async()
        assert data == b"ASYNC TEST"

        # Partial read (max larger than available)
        pty_serial.control.write(b"HELLO")
        data = await conn.read_async()
        assert data == b"HELLO"


async def test_async_drain(pty_serial):
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        # Basic drain to completion
        conn.write(b"DRAIN TEST")
        result = await conn.drain_async()
        assert result is True
        assert pty_serial.control.read(256) == b"DRAIN TEST"


async def test_async_read_and_write_concurrent(pty_serial):
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:

        async def reader():
            return await conn.read_async()

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
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        start = time.monotonic()
        data = conn.read_sync(timeout=0.1)
        elapsed = time.monotonic() - start
        assert data == b""
        assert 0.05 <= elapsed <= 0.5


@pytest.mark.parametrize("timeout", [0, -1])
def test_read_sync_zero_or_negative_timeout(pty_serial, timeout):
    """Test that zero or negative timeout returns immediately with no data."""
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        start = time.monotonic()
        data = conn.read_sync(timeout=timeout)
        elapsed = time.monotonic() - start
        assert data == b""
        assert elapsed < 0.1


#
# Output buffer behavior
#


def test_drain_sync_completes(pty_serial):
    """Test drain_sync completes when buffer empties."""
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        conn.write(b"SMALL")
        result = conn.drain_sync(timeout=5.0)
        assert result is True
        assert pty_serial.control.read(256) == b"SMALL"


def test_drain_sync_zero_timeout(pty_serial):
    """Test drain_sync with zero timeout returns immediately."""
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        conn.write(b"TEST DATA")
        result = conn.drain_sync(timeout=0)
        assert isinstance(result, bool)


def test_outgoing_size_after_drain(pty_serial):
    """Test outgoing_size is zero after drain completes."""
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        assert conn.outgoing_size() == 0
        conn.write(b"1234567890")
        conn.drain_sync(timeout=5.0)
        assert conn.outgoing_size() == 0


#
# Connection close and exception handling
#


def test_operations_after_close_raise(pty_serial):
    """Test that read/write/drain after close raises SerialIoClosed."""
    conn = ok_serial.SerialConnection(port=pty_serial.path)
    conn.close()

    with pytest.raises(ok_serial.SerialIoClosed):
        conn.read_sync(timeout=1.0)

    with pytest.raises(ok_serial.SerialIoClosed):
        conn.write(b"test")

    with pytest.raises(ok_serial.SerialIoClosed):
        conn.drain_sync(timeout=1.0)


async def test_async_operations_after_close_raise(pty_serial):
    """Test that async read/drain after close raises SerialIoClosed."""
    conn = ok_serial.SerialConnection(port=pty_serial.path)
    conn.close()

    with pytest.raises(ok_serial.SerialIoClosed):
        await conn.read_async()

    with pytest.raises(ok_serial.SerialIoClosed):
        await conn.drain_async()


def test_context_manager_closes_on_exit(pty_serial):
    """Test that context manager properly closes connection."""
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        conn.write(b"test")
        conn.drain_sync(timeout=1.0)

    with pytest.raises(ok_serial.SerialIoClosed):
        conn.read_sync(timeout=0.1)


def test_multiple_close_is_safe(pty_serial):
    """Test that calling close multiple times is safe."""
    conn = ok_serial.SerialConnection(port=pty_serial.path)
    conn.close()
    conn.close()
    conn.close()


#
# Multi-threaded access tests
#


def test_concurrent_reads_and_writes(pty_serial):
    """Test concurrent read and write from multiple threads."""
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        results = {"read": None, "write": None}
        errors = []

        def reader():
            try:
                results["read"] = conn.read_sync(timeout=5.0)
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
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
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
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        data = b"Y" * 512
        pty_serial.control.write(data)

        received = b""
        while len(received) < 512:
            received += await conn.read_async()
        assert received == data


#
# Serial signals (DTR, RTS, etc.)
#


def test_get_signals(pty_serial, mocker):
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        mocker.patch.object(serial.Serial, "dtr", new_callable=lambda: True)
        mocker.patch.object(serial.Serial, "dsr", new_callable=lambda: False)
        mocker.patch.object(serial.Serial, "cts", new_callable=lambda: True)
        mocker.patch.object(serial.Serial, "rts", new_callable=lambda: False)
        mocker.patch.object(serial.Serial, "ri", new_callable=lambda: True)
        mocker.patch.object(serial.Serial, "cd", new_callable=lambda: False)
        mocker.patch.object(
            serial.Serial, "break_condition", new_callable=lambda: True
        )

        signals = conn.get_signals()
        assert isinstance(signals, ok_serial.SerialControlSignals)
        assert signals == ok_serial.SerialControlSignals(
            True, False, True, False, True, False, True
        )


def test_set_signals(pty_serial, mocker):
    with ok_serial.SerialConnection(port=pty_serial.path) as conn:
        PMock = mocker.PropertyMock
        mock_dtr = mocker.patch.object(serial.Serial, "dtr", new_callable=PMock)
        mock_dsr = mocker.patch.object(serial.Serial, "dsr", new_callable=PMock)
        mock_rts = mocker.patch.object(serial.Serial, "rts", new_callable=PMock)
        mock_cts = mocker.patch.object(serial.Serial, "cts", new_callable=PMock)
        mock_break = mocker.patch.object(
            serial.Serial, "break_condition", new_callable=PMock
        )

        conn.set_signals(dtr=True, rts=False, send_break=True)
        mock_dtr.assert_called_with(True)
        mock_dsr.assert_not_called()
        mock_rts.assert_called_with(False)
        mock_cts.assert_not_called()
        mock_break.assert_called_with(True)


def test_signals_after_close_raises(pty_serial):
    conn = ok_serial.SerialConnection(port=pty_serial.path)
    conn.close()

    with pytest.raises(ok_serial.SerialIoClosed):
        conn.get_signals()

    with pytest.raises(ok_serial.SerialIoClosed):
        conn.set_signals(dtr=True)
