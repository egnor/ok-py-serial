import ok_serial

import asyncio
import contextlib
import dataclasses
import logging
import os
import re
import sys
import termios
import time
import typing

from ok_serial.terminal.chunker import TerminalChunker, chunk_to_bytes
from ok_serial.terminal.decorator import TerminalDecorator
from ok_serial._timeout_math import from_deadline


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


class _TerminalSession:
    async def run(self, opts: SerialTerminalOptions) -> None:
        async with contextlib.AsyncExitStack() as cleanup:
            self._event_loop = asyncio.get_running_loop()
            self._new_data_event = asyncio.Event()
            self._decorator: TerminalDecorator | None = None
            self._stop_requested = False

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
                    patch_args = (sys.stderr, "write", self._tsafe_decor_stderr)
                    patch_context = _monkeypatch_context(*patch_args)
                    cleanup.enter_context(patch_context)  # before raw mode!

                cleanup.enter_context(_raw_tty_context(0))
                cleanup.enter_context(_raw_tty_context(1))
                cleanup.callback(self._shutdown_decorator)

            task_group = await cleanup.enter_async_context(asyncio.TaskGroup())
            task_group.create_task(self._read_from_stdin())
            task_group.create_task(self._run_serial_monitor(opts))

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

        # use Fancy Terminal if available, else relay raw data directly
        if self._decorator:
            self._update_decorator_terminal()
        else:
            if self._serial and self._stdin_chunks:
                chunks, self._stdin_chunks = self._stdin_chunks, []
                stdin_bytes = b"".join(chunk_to_bytes(c) for c in chunks)
                self._serial.write(stdin_bytes)
            if self._serial_chunks:
                chunks, self._serial_chunks = self._serial_chunks, []
                serial_bytes = b"".join(chunk_to_bytes(c) for c in chunks)
                sys.stdout.buffer.write(serial_bytes)
                sys.stdout.flush()

    def _update_decorator_terminal(self) -> None:
        # use the decorator for "fancy" terminal output
        assert self._decorator
        decor = self._decorator
        timestamp = time.monotonic()

        stdin_chunks, self._stdin_chunks = self._stdin_chunks, []
        decor.add_from_terminal.extend(stdin_chunks)

        # at exit, wait a bit for query replies to avoid hitting the shell
        if self._stop_requested:
            decor.update(timestamp)
            if (qtime := decor.pending_query_time) and timestamp < qtime + 0.5:
                return  # keep waiting for reply, do no other processing
            else:
                raise SystemExit(0)

        decor_chunks: list[bytes | str]
        if self._serial is not self._last_serial:
            if self._last_serial:
                decor_chunks = [
                    *(b"\x1b[1;37;41m", "▶ Disconnected", b"\x1b[22m", " ┊ "),
                    self._last_serial.port_name,
                    b"\x1b[K",
                ]
                decor.add_above.append(decor_chunks)
            if self._serial:
                decor_chunks = [
                    *(b"\x1b[1;30;42m", "▶ Connected", b"\x1b[22m", " ┊ "),
                    f"{self._serial.port_name} ┊ ",
                    f"{self._serial.opts.baud}bps ┊ ",
                    f"{self._serial.opts.sharing}",
                    b"\x1b[K",
                ]
                decor.add_above.append(decor_chunks)
            self._last_serial = self._serial

        def sig_tag(fg: int, bg: int, name: str, v: bool) -> list[bytes | str]:
            name = name.upper() if v else name.lower()
            fg, bg, bold, style = (fg, bg, 1, 29) if v else (37, 40, 2, 9)
            return [
                *(b"\x1b[37;%dm" % bg, "▌"),
                *(b"\x1b[%d;%d;%d;%dm" % (bold, style, fg, bg), name),
                *(b"\x1b[22;29;37;%dm" % bg, "▐"),
                b"\x1b[30;47m",
            ]

        if self._serial_signals and self._serial_signals != self._last_signals:
            sig = self._last_signals = self._serial_signals
            decor_chunks = [
                *(b"\x1b[30;47m", "▸ out "),
                *sig_tag(30, 46, "dtr", sig.dtr),
                *sig_tag(30, 46, "rts", sig.rts),
                *sig_tag(30, 46, "break", sig.sending_break),
                " ┊ in ",
                *sig_tag(37, 44, "dsr", sig.dsr),
                *sig_tag(37, 44, "cts", sig.cts),
                *sig_tag(37, 44, "ri", sig.ri),
                *sig_tag(37, 44, "cd", sig.cd),
                b"\x1b[K",
            ]
            decor.add_above.append(decor_chunks)

        serial_chunks, self._serial_chunks = self._serial_chunks, []
        decor.add_base.extend(serial_chunks)
        decor.update(timestamp)

        from_term, decor.out_from_terminal = decor.out_from_terminal, []
        for chunk in from_term:
            if chunk == b"\x1d":  # ctrl-]
                pass  # TODO: menu
            elif chunk == b"\x1c":  # ctrl-\
                decor.add_above.append(
                    [b"\x1b[1;37;41m", "▶ Quit (ctrl-\\ pressed)", b"\x1b[K"]
                )
                decor.set_right.clear()
                decor.set_below.clear()
                self._stop_requested = True
            elif self._serial:
                self._serial.write(chunk_to_bytes(chunk))

        decor.update(timestamp)  # pick up output from input
        to_term, decor.out_to_terminal = decor.out_to_terminal, []
        sys.stdout.buffer.write(b"".join(chunk_to_bytes(c) for c in to_term))
        sys.stdout.flush()

    def _shutdown_decorator(self) -> None:
        assert self._decorator
        self._decorator.shutdown()
        chunks = self._decorator.out_to_terminal
        try:
            sys.stdout.buffer.write(b"".join(chunk_to_bytes(c) for c in chunks))
            sys.stdout.flush()
        except OSError:
            pass  # ignore output write errors in shutdown

    def _tsafe_decor_stderr(self, data: str) -> None:
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


@contextlib.asynccontextmanager
async def _async_reader_context(
    stream: typing.IO,
) -> typing.AsyncIterator[asyncio.StreamReader]:
    reader = asyncio.StreamReader()
    loop = asyncio.get_running_loop()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.connect_read_pipe(lambda: protocol, stream)
    try:
        yield reader
    finally:
        transport.close()


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
def _monkeypatch_context(obj: object, attr: str, val) -> typing.Iterator:
    save = getattr(obj, attr, None)
    try:
        setattr(obj, attr, val)
        yield save
    finally:
        setattr(obj, attr, save)
