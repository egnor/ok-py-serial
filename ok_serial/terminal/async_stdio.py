import asyncio
import contextlib
import logging
import os
import signal
import termios
import typing

AsyncReader = typing.Callable[[int], typing.Awaitable[bytes]]
AsyncWriter = typing.Callable[[bytes], typing.Awaitable[None]]

# These deliberately never set O_NONBLOCK. It belongs to the whole open file
# description, which stdin typically shares with stdout and stderr, so setting
# it makes unrelated buffered writes (eg. from logging) fail with EAGAIN and
# quietly lose whatever the buffer had already swallowed. Instead, wait for
# the fd to be ready and then do a plain blocking read or write.
#
# Waiting means the event loop's selector, which rejects fds it can't poll
# (regular files, /dev/null) -- but those are exactly the fds that are always
# ready, so a rejection just means "no need to wait". Nothing here needs a
# thread, nor a way to cancel one parked in blocking I/O.

# A ready fd promises room for *some* output, not all of it, and a blocking
# write doesn't return short -- it stays until everything is out. So writes go
# a chunk at a time, re-checking readiness; poll() promises a pipe at least
# PIPE_BUF bytes of room, which makes a chunk this size safe to hand it.
_WRITE_CHUNK = 4096


def _readiness_waiter(
    add: typing.Callable, remove: typing.Callable, fd: int
) -> typing.Callable[[], typing.Awaitable[None]]:
    """Returns an async function that waits for an fd to be ready, using the
    event loop's add_reader/remove_reader or add_writer/remove_writer, and
    that returns immediately for fds the loop's selector can't poll."""

    pollable = True

    async def wait_ready() -> None:
        nonlocal pollable
        if not pollable:
            return  # unpollable fds are always ready; don't wait for Godot
        future = asyncio.get_running_loop().create_future()
        try:
            add(fd, future.set_result, None)
        except OSError:  # eg. EPERM for a regular file or /dev/null
            logging.debug("FD %d can't be polled, treating as ready", fd)
            pollable = False
            return
        try:
            await future
        finally:
            remove(fd)

    return wait_ready


@contextlib.asynccontextmanager
async def async_reader_context(
    stream: typing.IO,
) -> typing.AsyncIterator[AsyncReader]:
    """Returns a context manager that produces an async read function for the
    stream, valid for the life of the context, leaving the stream functional."""

    loop = asyncio.get_running_loop()
    fd = stream.fileno()
    lock = asyncio.Lock()  # reads are all-or-nothing relative to each other
    wait_readable = _readiness_waiter(loop.add_reader, loop.remove_reader, fd)

    async def read(size: int) -> bytes:
        async with lock:
            while True:
                await wait_readable()
                try:
                    return os.read(fd, size)
                except BlockingIOError:
                    pass  # someone else set O_NONBLOCK; wait and retry

    yield read


@contextlib.asynccontextmanager
async def async_writer_context(
    stream: typing.IO,
) -> typing.AsyncIterator[AsyncWriter]:
    """Returns a context manager that produces an async write function for the
    stream, valid for the life of the context, leaving the stream functional."""

    # NOT asyncio.connect_write_pipe (as async_reader_context is not
    # connect_read_pipe): besides the readiness problem described above, it
    # rejects regular files outright, and its buffering can drop output if the
    # loop stops before the transport drains (and StreamWriter.wait_closed()
    # doesn't work on a write-only protocol).
    loop = asyncio.get_running_loop()
    fd = stream.fileno()
    lock = asyncio.Lock()  # writes are all-or-nothing relative to each other
    wait_writable = _readiness_waiter(loop.add_writer, loop.remove_writer, fd)

    async def write(data: bytes) -> None:
        async with lock:
            view = memoryview(data)
            while view:
                await wait_writable()
                try:
                    view = view[os.write(fd, view[:_WRITE_CHUNK]) :]
                except BlockingIOError:
                    pass  # someone else set O_NONBLOCK; wait and retry

    yield write


@contextlib.contextmanager
def async_signal_handler_context(
    sig: signal.Signals, handler: typing.Callable[[signal.Signals], None]
) -> typing.Iterator[None]:
    """Returns a context manager that installs an asyncio unix signal handler
    at entry and uninstalls it on exit."""

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(sig, lambda: handler(sig))
    try:
        yield
    finally:
        loop.remove_signal_handler(sig)


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
