import ok_serial

import asyncio
import contextlib
import dataclasses
import logging
import os
import re
import select
import signal
import sys
import termios
import time
import typing

from ok_serial.terminal.chunker import TerminalChunker, chunk_to_bytes
from ok_serial.terminal.decorator import TerminalDecorator
from ok_serial.terminal.keyboard import chunk_to_key_event
from ok_serial._timeout_math import from_deadline, to_deadline


@dataclasses.dataclass(frozen=True)
class SerialTerminalOptions:
    match: str
    copts: ok_serial.SerialConnectionOptions
    mopts: ok_serial.SerialMonitorOptions
    plain: bool = False


def run_terminal(opts: SerialTerminalOptions):
    """Synchronous wrapper for `run_terminal_async`"""
    asyncio.run(run_terminal_async(opts))


async def run_terminal_async(opts: SerialTerminalOptions):
    """Runs an interactive terminal communicating with a serial monitor"""
    await _TerminalSession().run(opts)


_NONPRINT_RX = re.compile("[\x00-\x1f]")  # unprintable characters to escape


class _SystemExitMessage(SystemExit):
    def __init__(self, code: int, message: str):
        super().__init__(code)
        self.message = message

    def __repr__(self) -> str:
        return f"SystemExitMessage({self.message!r}, {self.code!r})"


class _TerminalSession:
    async def run(self, opts: SerialTerminalOptions) -> None:
        sys.stdout.flush()  # all output goes through _write_stdout from here
        async with contextlib.AsyncExitStack() as cleanup:
            self._event_loop = asyncio.get_running_loop()
            self._new_data_event = asyncio.Event()
            self._decorator: TerminalDecorator | None = None
            self._unix_signal_received: signal.Signals | None = None

            self._serial: ok_serial.SerialConnection | None = None
            self._serial_signals: ok_serial.SerialControlSignals | None = None
            self._last_serial: ok_serial.SerialConnection | None = None
            self._last_signals: ok_serial.SerialControlSignals | None = None

            self._stdin_chunks: list[bytes | str] = []
            self._serial_chunks: list[bytes | str] = []
            self._stderr_buffer = ""

            # if stdin and stdout are the same terminal, do Fancy Terminal Stuff
            if not opts.plain and os.isatty(1) and os.stat(0) == os.stat(1):
                intro_chunks: list[bytes | str] = [
                    b"\x1b[30;46m",
                    f"▸ {ok_serial.__package__} v{ok_serial.__version__} ┊ ",
                    *(b"\x1b[1m", "ctrl-]", b"\x1b[22m", " for menu ┊ "),
                    *(b"\x1b[1m", "ctrl-\\", b"\x1b[22m", " to quit "),
                    b"\x1b[K",
                ]
                self._decorator = TerminalDecorator()
                self._decorator.add_above.append(intro_chunks)

                if os.stat(1) == os.stat(2):
                    patch_args = (sys.stderr, "write", self._stderr_write)
                    patch_context = _monkeypatch_context(*patch_args)
                    cleanup.enter_context(patch_context)  # before raw mode!

                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    cb = self._on_unix_signal
                    sig_context = _unix_signal_handler_context(sig, cb)
                    cleanup.enter_context(sig_context)

                cleanup.enter_context(_raw_tty_context(0))
                cleanup.enter_context(_raw_tty_context(1))

            task_group = await cleanup.enter_async_context(asyncio.TaskGroup())
            task_group.create_task(self._read_from_stdin())
            task_group.create_task(self._run_serial_monitor(opts))

            if self._decorator:  # clean up decorator while stdin reader
                cleanup.push_async_exit(self._async_decorator_exit)

            while True:
                await self._main_loop()

    async def _main_loop(self) -> None:
        try:
            async with asyncio.timeout(0.25):
                await self._new_data_event.wait()
        except TimeoutError:
            pass

        await asyncio.sleep(0)  # let logs updates, etc. happen
        self._new_data_event.clear()
        if unix := self._unix_signal_received:
            raise _SystemExitMessage(1, f"{unix.name} received")

        # use Fancy Terminal if available, else relay raw data directly
        if self._decorator:
            self._update_decorator_terminal()
        else:
            self._update_plain_terminal()

    def _update_plain_terminal(self) -> None:
        if self._serial and self._stdin_chunks:
            chunks, self._stdin_chunks = self._stdin_chunks, []
            stdin_bytes = b"".join(chunk_to_bytes(c) for c in chunks)
            self._serial.write(stdin_bytes)
        if self._serial_chunks:
            chunks, self._serial_chunks = self._serial_chunks, []
            serial_bytes = b"".join(chunk_to_bytes(c) for c in chunks)
            _write_stdout(serial_bytes)

    def _update_decorator_terminal(self) -> None:
        assert self._decorator  # use the decorator for "fancy" terminal output
        decor = self._decorator
        timestamp = time.monotonic()

        stdin_chunks, self._stdin_chunks = self._stdin_chunks, []
        decor.add_from_terminal.extend(stdin_chunks)

        line: list[bytes | str]
        if self._serial is not self._last_serial:
            if self._last_serial:
                line = [
                    *(b"\x1b[1;37;41m", "▶ Disconnected", b"\x1b[22m", " ┊ "),
                    self._last_serial.port_name,
                    b"\x1b[K",
                ]
                decor.reset()
                decor.add_above.append(line)
            if self._serial:
                line = [
                    *(b"\x1b[1;30;42m", "▶ Connected", b"\x1b[22m", " ┊ "),
                    f"{self._serial.port_name} ┊ ",
                    f"{self._serial.opts.baud}bps ┊ ",
                    f"{self._serial.opts.sharing}",
                    b"\x1b[K",
                ]
                decor.add_above.append(line)
            self._last_serial = self._serial

        def ser_tag(fg: int, bg: int, name: str, v: bool) -> list[bytes | str]:
            name = name.upper() if v else name.lower()
            fg, bg, bold, style = (fg, bg, 1, 29) if v else (37, 40, 2, 9)
            return [
                *(b"\x1b[37;%dm" % bg, "▌"),
                *(b"\x1b[%d;%d;%d;%dm" % (bold, style, fg, bg), name),
                *(b"\x1b[22;29;37;%dm" % bg, "▐"),
                b"\x1b[30;47m",
            ]

        if self._serial_signals and self._serial_signals != self._last_signals:
            self._last_signals = self._serial_signals
            line = [
                *(b"\x1b[30;47m", "▸ out "),
                *ser_tag(30, 46, "dtr", self._serial_signals.dtr),
                *ser_tag(30, 46, "rts", self._serial_signals.rts),
                *ser_tag(30, 46, "break", self._serial_signals.sending_break),
                " ┊ in ",
                *ser_tag(37, 44, "dsr", self._serial_signals.dsr),
                *ser_tag(37, 44, "cts", self._serial_signals.cts),
                *ser_tag(37, 44, "ri", self._serial_signals.ri),
                *ser_tag(37, 44, "cd", self._serial_signals.cd),
                b"\x1b[K",
            ]
            decor.add_above.append(line)

        serial_chunks, self._serial_chunks = self._serial_chunks, []
        decor.add_base.extend(serial_chunks)
        decor.update(timestamp)

        from_term, decor.out_from_terminal = decor.out_from_terminal, []
        for chunk in from_term:
            key_event = chunk_to_key_event(chunk)
            key_text = key_event.text if key_event else ""
            if key_text == "\x1d":  # ctrl-]
                pass  # TODO: menu
            elif key_text == "\x1c":  # ctrl-\
                raise _SystemExitMessage(0, "ctrl-\\ pressed")
            elif self._serial:
                self._serial.write(chunk_to_bytes(chunk))

        decor.update(timestamp)  # pick up output from input
        to_term, decor.out_to_terminal = decor.out_to_terminal, []
        _write_stdout(b"".join(chunk_to_bytes(c) for c in to_term))

    async def _async_decorator_exit(self, exc_type, exc, tb) -> None:
        assert self._decorator
        self._decorator.reset()  # back to main screen, add blank line, etc.

        if isinstance(exc, _SystemExitMessage):
            line: list[bytes | str] = [
                b"\x1b[1;37;41m",
                f"▶ Quit ({exc.message})",
                b"\x1b[K",
            ]
            self._decorator.add_above.append(line)
            self._decorator.update(time.monotonic())
            self._decorator.reset()

        try:
            chunks = self._decorator.out_to_terminal
            _write_stdout(b"".join(chunk_to_bytes(c) for c in chunks))
        except OSError:
            pass  # ignore output write errors in shutdown

        # wait a bit and conusme query replies to stop them hitting the shell
        deadline = to_deadline(0.25)
        while self._decorator.pending_query_time:
            async with asyncio.timeout(from_deadline(deadline)):
                await self._new_data_event.wait()
            self._new_data_event.clear()
            self._decorator.add_from_terminal.extend(self._stdin_chunks)
            self._decorator.update(time.monotonic())
            self._stdin_chunks = []

    def _stderr_write(self, data: str) -> None:
        def esc_char(m: re.Match[str]) -> str:
            return m.group().encode("unicode_escape").decode("ascii")

        async def in_loop() -> None:
            buffer, self._stderr_buffer = self._stderr_buffer + data, ""
            for line in buffer.splitlines(keepends=True):
                if not line.endswith(("\n", "\r")):
                    self._stderr_buffer += line  # partial line
                elif self._decorator:
                    msg = _NONPRINT_RX.sub(esc_char, "▸ " + line.rstrip())
                    color, clear = b"\x1b[30;47m", b"\x1b[K"
                    self._decorator.add_above.append([color, msg, clear])
            self._new_data_event.set()

        asyncio.run_coroutine_threadsafe(in_loop(), self._event_loop)

    async def _read_from_stdin(self) -> None:
        async with _async_reader_context(sys.stdin) as inp:
            chunker = TerminalChunker()
            while True:
                try:
                    timeout = from_deadline(chunker.data_deadline)
                    async with asyncio.timeout(timeout):
                        if not (data := await inp.read(256)):
                            raise EOFError("Input closed")
                        chunker.add_data(data, time.monotonic())
                except TimeoutError:
                    chunker.add_data(b"", time.monotonic())
                if chunker.chunks:
                    self._stdin_chunks.extend(chunker.chunks)
                    self._new_data_event.set()
                    chunker.chunks.clear()

    async def _run_serial_monitor(self, opts: SerialTerminalOptions) -> None:
        with ok_serial.SerialConnectionMonitor(
            opts.match, copts=opts.copts, mopts=opts.mopts
        ) as monitor:
            while True:
                self._serial = await monitor.connect_async()
                self._new_data_event.set()
                try:
                    await self._read_from_serial()
                except ok_serial.SerialIoException as ex:
                    logging.warning("%s", ex)
                    self._serial = None
                    self._serial_signals = None
                    self._new_data_event.set()

    async def _read_from_serial(self) -> None:
        assert self._serial
        chunker = TerminalChunker()
        while True:
            try:
                # cap timeout to 0.2s for control signal polling
                timeout = min(0.2, from_deadline(chunker.data_deadline))
                async with asyncio.timeout(timeout):
                    data = await self._serial.read_async()
                    chunker.add_data(data, data.monotonic_time)
            except TimeoutError:
                chunker.add_data(b"", time.monotonic())

            try:
                signals = self._serial.get_signals()
            except ok_serial.SerialIoUnsupported:
                pass  # could be a pty
            else:
                if signals != self._serial_signals:
                    self._serial_signals = signals
                    self._new_data_event.set()

            if chunker.chunks:
                self._serial_chunks.extend(chunker.chunks)
                self._new_data_event.set()
                chunker.chunks.clear()

    def _on_unix_signal(self, sig: signal.Signals) -> None:
        if not self._unix_signal_received:
            self._unix_signal_received = sig
            self._new_data_event.set()


def _write_stdout(data: bytes) -> None:
    # asyncio sets O_NONBLOCK on stdin, whose open file description is
    # usually shared with stdout, so buffered writes can fail with EAGAIN;
    # write the fd directly, waiting for writability as needed
    view = memoryview(data)
    while view:
        try:
            view = view[os.write(1, view) :]
        except BlockingIOError:
            select.select([], [1], [])


@contextlib.asynccontextmanager
async def _async_reader_context(
    stream: typing.IO,
) -> typing.AsyncIterator[asyncio.StreamReader]:
    # connect_read_pipe takes ownership of the file object and closes it with
    # the transport, so give it a dup rather than (say) sys.stdin itself
    fd = stream.fileno()
    was_blocking = os.get_blocking(fd)
    dup_stream = os.fdopen(os.dup(fd), "rb", buffering=0)

    reader = asyncio.StreamReader()
    loop = asyncio.get_running_loop()
    protocol = asyncio.StreamReaderProtocol(reader)
    try:
        transport, _ = await loop.connect_read_pipe(
            lambda: protocol, dup_stream
        )
    except BaseException:
        dup_stream.close()
        raise

    try:
        yield reader
    finally:
        transport.close()
        # asyncio set O_NONBLOCK on the open file description, which the dup
        # (and often stdout/stderr) shares with `stream`; undo that here
        os.set_blocking(fd, was_blocking)


@contextlib.contextmanager
def _raw_tty_context(fd: typing.Literal[0, 1, 2]) -> typing.Iterator[bool]:
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


@contextlib.contextmanager
def _unix_signal_handler_context(
    sig: signal.Signals, handler: typing.Callable[[signal.Signals], None]
) -> typing.Iterator[None]:
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(sig, lambda: handler(sig))
    try:
        yield
    finally:
        loop.remove_signal_handler(sig)


@contextlib.contextmanager
def _monkeypatch_context(obj: object, attr: str, val) -> typing.Iterator:
    save = getattr(obj, attr, None)
    try:
        setattr(obj, attr, val)
        yield save
    finally:
        setattr(obj, attr, save)
