#!/usr/bin/env python3

"""CLI tool to scan serial ports and/or communicate with them"""

import datetime
import logging
import re
import sys

try:
    import click
    import ok_logging_setup
except ModuleNotFoundError:
    print("\n⚠️ Try: pip install 'ok-serial[cli]'\n", file=sys.stderr)
    raise

import ok_serial
import ok_serial.terminal

ok_logging_setup.install({"OK_LOGGING_LEVEL": "info"})
ok_logging_setup.skip_traceback_for(ok_serial.SerialMatcherInvalid)
ok_logging_setup.skip_traceback_for(ok_serial.SerialScanException)


@click.group()
def main():
    pass


@main.command()
@click.argument("match", nargs=-1)
@click.option("-1", "--one", is_flag=True)
@click.option("-n", "--print-name", is_flag=True)
@click.option("-v", "--print-verbose", is_flag=True)
@click.option("-w", "--wait-time", default=0.0)
def list_command(
    match: tuple[str, ...],
    one: bool = False,
    print_name: bool = False,
    print_verbose: bool = False,
    wait_time: float = 0.0,
):
    tracker = ok_serial.SerialPortTracker(match=" ".join(match))
    if (expr := str(tracker.matcher)) and wait_time:
        logging.info(
            "🔎 Finding serial ports (%.2fs timeout): %r",
            wait_time,
            str(tracker.matcher),
        )
    elif expr:
        logging.info("🔎 Finding serial ports: %r", expr)
    elif wait_time:
        logging.info("🔎 Finding serial ports (%.2fs timeout)", wait_time)
    else:
        logging.info("🔎 Finding serial ports...")

    found = tracker.find_sync(timeout=wait_time)
    num = len(found)
    if num == 0:
        if expr := str(tracker.matcher):
            ok_logging_setup.exit(f"🚫 No serial ports match {expr!r}")
        else:
            ok_logging_setup.exit("❌ No serial ports found")

    logging.info("🔌 %d serial port%s found", num, "" if num == 1 else "s")

    matcher = tracker.matcher
    if one and num != 1:
        ok_logging_setup.exit(
            f"{num} serial ports found, only --one allowed:"
            + "".join(f"\n  {format_line(p, matcher)}" for p in found)
        )
    if print_name:
        for port in found:
            click.echo(port.name)
    elif print_verbose:
        for port in found:
            click.echo(format_detail(port, matcher) + "\n")
    else:
        for port in found:
            click.echo(format_line(port, matcher))


@main.command()
@click.argument("match", nargs=-1)
@click.argument("baud", type=int)
@click.option("-w", "--wait-time", default=0.0)
def term_command(match: tuple[str, ...], baud: int, wait_time: float = 0):
    ok_serial.terminal.run_terminal(
        match=" ".join(match), baud=baud, wait_time=wait_time
    )


def format_line(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher
):
    main_keys = "device tid subsystem vid_pid description serial_number".split()
    words = []
    for k in main_keys:
        if v := format_value(port, matcher, k):
            words.append(v)

    if age := format_age(port):
        words.append(age)

    for k, v in port.attr.items():
        if matcher.attr_hit(port, k) and k not in main_keys:
            if not (k == "name" and matcher.attr_hit(port, "device")):
                words.append(f"{k}={format_value(port, matcher, k)}")

    return " ".join(words)


def format_detail(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher
) -> str:
    label = f"Port: {format_value(port, matcher, 'device')}"
    if tid := format_value(port, matcher, "tid"):
        label += f" {tid}"
    if age := format_age(port):
        label += f" {age}"
    return label + "".join(
        f"\n  {k}={format_value(port, matcher, k)}" for k in port.attr
    )


def format_value(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher, k: str
) -> str:
    if v := port.attr.get(k, ""):
        v = repr(v) if re.search(r"""[\s!"'*=?\\]""", v) else v
        return v + ("✅" if matcher.attr_hit(port, k) else "")
    return ""


def format_age(port: ok_serial.SerialPort) -> str:
    try:
        dt = datetime.datetime.fromisoformat(port.attr.get("time", ""))
    except ValueError:
        return ""
    return format_timedelta(datetime.datetime.now() - dt)


def format_timedelta(d: datetime.timedelta) -> str:
    if d.days < 0:
        return f"-{format_timedelta(-d)}"
    h, m, s = d.seconds // 3600, (d.seconds % 3600) // 60, d.seconds % 60
    if d.days:
        return f"{d.days}d+{h:02}:{m:02}:{s:02}s"
    elif h:
        return f"{h}:{m:02}:{s:02}s"
    elif m:
        return f"{m}:{s:02}s"
    else:
        return f"{d.seconds + d.microseconds * 1e-6:.2f}s"


if __name__ == "__main__":
    main()
