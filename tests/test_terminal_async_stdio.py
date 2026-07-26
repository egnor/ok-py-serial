import asyncio
import io
import os
import pty
import pytest
import threading
import tty

from ok_serial.terminal.async_stdio import AsyncReader, AsyncWriter


@pytest.fixture
def pty_pair():
    ctrl_fd, sim_fd = pty.openpty()
    tty.setraw(sim_fd)  # as the terminal does; no line buffering or echo
    with os.fdopen(ctrl_fd, "r+b", buffering=0) as ctrl:
        with os.fdopen(sim_fd, "r+b", buffering=0) as sim:
            yield ctrl, sim


async def test_read_from_tty(pty_pair):
    ctrl, sim = pty_pair
    reader = AsyncReader(sim)
    ctrl.write(b"typed")
    assert await asyncio.wait_for(reader.read(256), 2) == b"typed"

    # with no data pending, the read waits rather than spinning or failing
    reading = asyncio.ensure_future(reader.read(256))
    await asyncio.sleep(0.05)
    assert not reading.done()
    ctrl.write(b"more")
    assert await asyncio.wait_for(reading, 2) == b"more"


async def test_read_from_unpollable_fds(tmp_path):
    # the event loop selector rejects these fds, but they never block either
    path = tmp_path / "input.txt"
    path.write_bytes(b"file contents")
    with open(path, "rb") as file:
        reader = AsyncReader(file)
        assert await asyncio.wait_for(reader.read(256), 2) == b"file contents"
        assert await asyncio.wait_for(reader.read(256), 2) == b""  # end of file

    with open("/dev/null", "rb") as null:
        reader = AsyncReader(null)
        assert await asyncio.wait_for(reader.read(256), 2) == b""  # end of file


async def test_io_leaves_blocking_mode_alone(pty_pair):
    # O_NONBLOCK belongs to the open file description that stdin shares with
    # stdout/stderr, so setting it would break unrelated buffered writes
    ctrl, sim = pty_pair
    assert os.get_blocking(sim.fileno())
    reader = AsyncReader(sim)
    writer = AsyncWriter(sim)
    ctrl.write(b"typed")
    assert await asyncio.wait_for(reader.read(256), 2) == b"typed"
    await asyncio.wait_for(writer.write(b"output"), 2)
    assert os.get_blocking(sim.fileno())


async def test_buffered_writes_survive_concurrent_reader(pty_pair):
    # the logging case: buffered output (eg. a logging stream) sharing the
    # terminal with the reader must not start failing with EAGAIN and
    # dropping whatever the buffer had already swallowed
    ctrl, sim = pty_pair
    lines = 400  # more than the terminal buffer holds, so it must block

    drained: list[bytes] = []

    def drain_terminal():  # a real terminal consumes output as it arrives
        while sum(chunk.count(b"ttyUSB") for chunk in drained) < lines:
            if not (chunk := ctrl.read(4096)):
                return
            drained.append(chunk)

    drainer = threading.Thread(target=drain_terminal, daemon=True)
    drainer.start()

    raw = io.FileIO(sim.fileno(), "wb", closefd=False)
    logs = io.TextIOWrapper(io.BufferedWriter(raw))
    reader = AsyncReader(sim)
    reading = asyncio.ensure_future(reader.read(256))  # reader still active
    await asyncio.sleep(0.05)
    for i in range(lines):
        print(f"scan found port /dev/ttyUSB{i:03d}", file=logs)
    logs.flush()
    reading.cancel()

    drainer.join(timeout=10)
    assert sum(chunk.count(b"ttyUSB") for chunk in drained) == lines


async def test_read_cancel_keeps_stream_usable(pty_pair):
    ctrl, sim = pty_pair
    reader = AsyncReader(sim)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(reader.read(256), 0.05)  # cancelled during wait

    # the cancelled read consumed nothing and left no reader registered
    ctrl.write(b"after cancel")
    assert await asyncio.wait_for(reader.read(256), 2) == b"after cancel"


async def test_write_to_tty(pty_pair):
    ctrl, sim = pty_pair
    writer = AsyncWriter(sim)
    await asyncio.wait_for(writer.write(b"output"), 2)
    assert ctrl.read(256) == b"output"


async def test_write_to_unpollable_fd(tmp_path):
    path = tmp_path / "output.txt"
    with open(path, "wb") as file:  # a regular file is never "writable"
        writer = AsyncWriter(file)
        await asyncio.wait_for(writer.write(b"file contents"), 2)
    assert path.read_bytes() == b"file contents"


async def test_write_waits_out_backpressure():
    # a full pipe must not stall the event loop, even though a blocking write
    # of the whole buffer would sit there until the far end drained it
    read_fd, write_fd = os.pipe()
    assert os.get_blocking(write_fd)
    data = bytes(range(256)) * 4096  # bigger than any pipe buffer
    ticks = 0

    async def tick_while_writing() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.001)
            ticks += 1

    async def drain_pipe() -> bytes:
        loop = asyncio.get_running_loop()
        got = bytearray()
        while len(got) < len(data):
            got += await loop.run_in_executor(None, os.read, read_fd, 4096)
        return bytes(got)

    ticker = asyncio.ensure_future(tick_while_writing())
    draining = asyncio.ensure_future(drain_pipe())
    try:
        with os.fdopen(write_fd, "wb", buffering=0) as pipe:
            writer = AsyncWriter(pipe)
            await asyncio.wait_for(writer.write(data), 10)
        assert await asyncio.wait_for(draining, 10) == data
        assert ticks > 0  # the event loop kept running during the write
    finally:
        ticker.cancel()
        os.close(read_fd)
