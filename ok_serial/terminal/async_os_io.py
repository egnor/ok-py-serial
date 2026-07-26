import asyncio
import contextlib
import logging
import os
import termios
import typing

log = logging.getLogger(__name__)

# Approach: Both reads and writes *attempt* to use the event loop; if
# loop.add_reader/writer rejects the fd (file, /dev/null, etc), proceed anyway.
#
# Other approaches considered
# - asyncio.streams.StreamReader/Writer: don't work on files, /dev/null, etc
# - asyncio.run_in_executor (or similar threading): cancellation is difficult
# - O_NONBLOCK: breaks other users of the file (eg. stderr writes to same tty)


class AsyncReader:
    def __init__(self, stream: typing.IO) -> None:
        self._fd = stream.fileno()
        self._lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()
        self._pollable = True

    async def read(self, size: int) -> bytes:
        async with self._lock:
            while True:
                if self._pollable:
                    future = self._loop.create_future()
                    try:
                        self._loop.add_reader(self._fd, future.set_result, None)
                        await future
                    except OSError:
                        log.debug("FD %d isn't pollable (reading)", self._fd)
                        self._pollable = False
                    finally:
                        self._loop.remove_reader(self._fd)

                with contextlib.suppress(BlockingIOError):
                    return os.read(self._fd, size)


class AsyncWriter:
    def __init__(self, stream: typing.IO) -> None:
        self._fd = stream.fileno()
        self._lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()
        self._pollable = True

    async def write(self, data: bytes) -> None:
        async with self._lock:
            while data:
                if self._pollable:
                    future = self._loop.create_future()
                    try:
                        self._loop.add_writer(self._fd, future.set_result, None)
                        await future
                    except OSError:
                        log.debug("FD %d isn't pollable (writing)", self._fd)
                        self._pollable = False
                    finally:
                        self._loop.remove_writer(self._fd)

                with contextlib.suppress(BlockingIOError):
                    data = data[os.write(self._fd, data[:256]) :]


@contextlib.contextmanager
def raw_tty_context(fd: typing.Literal[0, 1, 2]) -> typing.Iterator[bool]:
    """Returns a context manager that, on entry, if the stdio fd (0, 1, 2)
    is a terminal, sets it to raw mode and restores original mode on exit."""

    try:
        old_attr = termios.tcgetattr(fd)
    except termios.error:
        logging.debug("FD %d is not a terminal, skipping raw mode", fd)
        yield False  # not a tty
        return

    if fd == 0:
        raw_cc = [int(i == termios.VMIN) for i in range(len(old_attr[6]))]
        raw_attr = [0, old_attr[1], 0, 0, *old_attr[4:6], raw_cc]
    else:
        raw_attr = [old_attr[0], 0, *old_attr[2:]]

    logging.debug("Setting tty fd=%d to raw mode", fd)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, raw_attr)
        yield True  # is a tty
    finally:
        logging.debug("Restoring tty fd=%d to original mode", fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attr)
