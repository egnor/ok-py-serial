import ok_serial

import asyncio
import contextlib
import dataclasses
import os
import re
import signal
import sys
import time

from ok_serial.terminal.async_stdio import (
    AsyncReader,
    AsyncWriter,
    raw_tty_context,
)
from ok_serial.terminal.chunker import TerminalChunker, chunk_to_bytes
from ok_serial.terminal.decorator import TerminalDecorator
from ok_serial.terminal.keyboard import chunk_to_key_event
from ok_serial._timeout_math import from_deadline, to_deadline

# TODO: maybe skip TerminalChunker entirely in plain (non-decorator) mode,
# passing raw bytes from serial to stdout; needs some dataflow restructuring
# (separate bytes/chunks pipelines depending on mode?)


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

            self._serial: ok_serial.SerialConnection | None = None
            self._serial_signals: ok_serial.SerialControlSignals | None = None
            self._last_serial: ok_serial.SerialConnection | None = None
            self._last_signals: ok_serial.SerialControlSignals | None = None

            self._stdin_chunks: list[bytes | str] = []
            self._serial_chunks: list[bytes | str] = []
            self._serial_error: ok_serial.SerialException | None = None
            self._serial_failed: ok_serial.SerialException | None = None

            self._stdout = AsyncWriter(sys.stdout)
            self._decorator: TerminalDecorator | None = None
            self._decorator_killed: signal.Signals | None = None
            self._decorator_stderr = ""

            # if stdin and stdout are the same terminal, do Fancy Terminal Stuff
            if not opts.plain and os.isatty(1) and os.stat(0) == os.stat(1):
                intro_chunks: list[bytes | str] = [
                    b"\x1b[37;44m",
                    f"▸ {ok_serial.__package__} v{ok_serial.__version__} ┊ ",
                    *(b"\x1b[1m", "ctrl-]", b"\x1b[22m", " for menu ┊ "),
                    *(b"\x1b[1m", "ctrl-\\", b"\x1b[22m", " to quit "),
                    b"\x1b[K",
                ]
                self._decorator = TerminalDecorator()
                self._decorator.add_above.append(intro_chunks)

                if os.stat(1) == os.stat(2):
                    save_write = sys.stderr.write
                    setattr(sys.stderr, "write", self._decorator_stderr_hook)
                    cleanup.callback(setattr, sys.stderr, "write", save_write)

                for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    cb = self._decorator_signal_hook
                    self._event_loop.add_signal_handler(s, cb, s)
                    cleanup.callback(self._event_loop.remove_signal_handler, s)

                cleanup.enter_context(raw_tty_context(0))
                cleanup.enter_context(raw_tty_context(1))

            task_group = await cleanup.enter_async_context(asyncio.TaskGroup())
            task_group.create_task(self._serial_monitor_task(opts))
            task_group.create_task(self._stdin_reader_task())

            if self._decorator:  # clean up decorator with stdin reader running
                cleanup.push_async_callback(self._async_decorator_cleanup)

            while True:
                await self._main_loop()

    async def _main_loop(self) -> None:
        try:
            async with asyncio.timeout(0.25):
                await self._new_data_event.wait()
        except TimeoutError:
            pass

        await asyncio.sleep(0)  # let logs updates arrive, etc.
        self._new_data_event.clear()

        if self._decorator:
            to_terminal = self._update_decorator_terminal()
        else:
            to_terminal = self._update_plain_terminal()

        await self._stdout.write(to_terminal)

    def _update_plain_terminal(self) -> bytes:
        if self._serial and self._stdin_chunks:
            chunks, self._stdin_chunks = self._stdin_chunks, []
            stdin_bytes = b"".join(chunk_to_bytes(c) for c in chunks)
            self._serial.write(stdin_bytes)
        chunks, self._serial_chunks = self._serial_chunks, []
        serial_bytes = b"".join(chunk_to_bytes(c) for c in chunks)
        return serial_bytes

    def _update_decorator_terminal(self) -> bytes:
        assert self._decorator  # use the decorator for "fancy" terminal output
        decor = self._decorator
        timestamp = time.monotonic()

        stdin_chunks, self._stdin_chunks = self._stdin_chunks, []
        decor.add_from_terminal.extend(stdin_chunks)

        line: list[bytes | str]
        if self._serial is not self._last_serial:
            if self._last_serial:
                decor.reset()
                line = [b"\x1b[1;30;43m", "▶ Disconnected", b"\x1b[22m"]
                if self._serial_error:
                    line.append(f" ┊ {self._serial_error}")
                else:
                    line.append(f" ┊ {self._last_serial.port_name}")
                decor.add_above.append([*line, b"\x1b[K"])
            if self._serial:
                line = [
                    *(b"\x1b[1;30;42m", "▶ Connected", b"\x1b[22m"),
                    f" ┊ {self._serial.port_name}",
                    f" ┊ {self._serial.opts.baud}bps",
                    f" ┊ {self._serial.opts.sharing}",
                ]
                decor.add_above.append([*line, b"\x1b[K"])
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
            ]
            decor.add_above.append([*line, b"\x1b[K"])

        if self._serial_failed:
            decor.reset()  # back to main screen, add blank line
            line = [
                *(b"\x1b[1;37;41m", "▶ Failed", b"\x1b[22m", " ┊ "),
                str(self._serial_failed),
            ]
            decor.add_above.append([*line, b"\x1b[K"])
            decor.update(timestamp)
            raise SystemExit(1)

        if self._decorator_killed:
            decor.reset()  # back to main screen, add blank line
            line = [
                *(b"\x1b[1;37;41m", "▶ Killed", b"\x1b[22m", " ┊ "),
                *(self._decorator_killed.name, b"\x1b[K"),
            ]
            decor.add_above.append(line)
            decor.update(timestamp)
            raise SystemExit(255)

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
                decor.reset()  # back to main screen, add blank line
                line = [
                    *(b"\x1b[1;37;44m", "▶ Quit", b"\x1b[22m"),
                    *(" ┊ (ctrl-\\ pressed)", b"\x1b[K"),
                ]
                decor.add_above.append(line)
                decor.update(timestamp)
                raise SystemExit(0)
            elif self._serial:
                self._serial.write(chunk_to_bytes(chunk))

        decor.update(timestamp)  # pick up output from input
        to_term, decor.out_to_terminal = decor.out_to_terminal, []
        return b"".join(chunk_to_bytes(c) for c in to_term)

    async def _async_decorator_cleanup(self) -> None:
        assert self._decorator
        self._decorator.reset()  # back to default terminal mode
        with contextlib.suppress(OSError):
            final = self._decorator.out_to_terminal
            await self._stdout.write(b"".join(chunk_to_bytes(c) for c in final))

        # wait a bit and consume query replies to stop them hitting the shell
        deadline = to_deadline(0.25)
        with contextlib.suppress(TimeoutError):  # give up if they don't come
            while self._decorator.pending_query_time:
                async with asyncio.timeout(from_deadline(deadline)):
                    await self._new_data_event.wait()
                self._new_data_event.clear()
                self._decorator.add_from_terminal.extend(self._stdin_chunks)
                self._decorator.update(time.monotonic())
                self._stdin_chunks = []

    def _decorator_stderr_hook(self, data: str) -> None:
        def esc_char(m: re.Match[str]) -> str:
            return m.group().encode("unicode_escape").decode("ascii")

        async def in_loop() -> None:
            buffer, self._decorator_stderr = self._decorator_stderr + data, ""
            for line in buffer.splitlines(keepends=True):
                if not line.endswith(("\n", "\r")):
                    self._decorator_stderr += line  # partial line
                elif self._decorator:
                    msg = _NONPRINT_RX.sub(esc_char, "▸ " + line.rstrip())
                    color, clear = b"\x1b[30;47m", b"\x1b[K"
                    self._decorator.add_above.append([color, msg, clear])
            self._new_data_event.set()

        asyncio.run_coroutine_threadsafe(in_loop(), self._event_loop)

    def _decorator_signal_hook(self, sig: signal.Signals) -> None:
        if not self._decorator_killed:
            self._decorator_killed = sig
            self._new_data_event.set()

    async def _stdin_reader_task(self) -> None:
        stdin = AsyncReader(sys.stdin)
        chunker = TerminalChunker()
        while True:
            try:
                timeout = from_deadline(chunker.data_deadline)
                async with asyncio.timeout(timeout):
                    data = await stdin.read(256)
                    if not data:  # with VMIN=1, means EOF
                        raise EOFError("EOF reading stdin")
                    chunker.add_data(data, time.monotonic())
            except TimeoutError:
                chunker.add_data(b"", time.monotonic())
            if chunker.chunks:
                self._stdin_chunks.extend(chunker.chunks)
                self._new_data_event.set()
                chunker.chunks.clear()

    async def _serial_monitor_task(self, opts: SerialTerminalOptions) -> None:
        with ok_serial.SerialConnectionMonitor(
            opts.match, copts=opts.copts, mopts=opts.mopts
        ) as monitor:
            try:
                while True:
                    self._serial = await monitor.connect_async()
                    self._serial_error = None
                    self._new_data_event.set()
                    try:
                        await self._read_from_serial()
                    except ok_serial.SerialIoException as ex:
                        self._serial_error = ex
                    finally:
                        self._serial = None
                        self._serial_signals = None
                        self._new_data_event.set()
            except ok_serial.SerialException as ex:
                self._serial_failed = ex  # permanent failure
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

            # ignore errors for signal fetch (eg. unimplemented on pty devs).
            with contextlib.suppress(ok_serial.SerialIoException):
                signals = self._serial.get_signals()
                if signals != self._serial_signals:
                    self._serial_signals = signals
                    self._new_data_event.set()

            if chunker.chunks:
                self._serial_chunks.extend(chunker.chunks)
                self._new_data_event.set()
                chunker.chunks.clear()
