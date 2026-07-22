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
from ok_serial._terminal import run_terminal, SerialTerminalOptions

ok_logging_setup.skip_traceback_for(OSError)  # includes SerialException
ok_logging_setup.install()


@click.group()
def main():
    pass


@main.command()
@click.argument("match", nargs=-1)
@click.option("--one", "-1", is_flag=True)
@click.option("--print-name", "-n", is_flag=True)
@click.option("--print-verbose", "-v", is_flag=True)
def list_command(
    match: tuple[str, ...],
    one: bool = False,
    print_name: bool = False,
    print_verbose: bool = False,
    wait_time: float = 0.0,
):
    """Print a list of available serial ports"""

    if spec := " ".join(match):
        logging.info("🔎 Finding serial ports: %r", spec)
    else:
        logging.info("🔎 Finding serial ports...")

    found = ok_serial.scan_serial_ports(spec)
    num = len(found)
    if num == 0:
        if spec:
            ok_logging_setup.exit(f"🚫 No serial ports match {spec!r}")
        else:
            ok_logging_setup.exit("❌ No serial ports found")

    logging.info("✅ %d serial port%s found", num, "" if num == 1 else "s")

    if one and num != 1:
        ok_logging_setup.exit(
            f"{num} serial ports found, only --one allowed:"
            + "".join(f"\n  {format_line(p)}" for p in found)
        )
    if print_name:
        for p in found:
            click.echo(p.name)
    elif print_verbose:
        for p in found:
            click.echo(format_detail(p) + "\n")
    else:
        for p in found:
            click.echo(format_line(p))


@main.command()
@click.argument("port_baud", metavar="PORT/BAUD", nargs=-1, required=True)
@click.option("--plain", "-p", is_flag=True)
@click.option("--reconnect", "-r", is_flag=True)
@click.option("--scan-time", "-s", default=0.0)
@click.option("--oblivious", "sharing", flag_value="oblivious")
@click.option("--polite", "sharing", flag_value="polite")
@click.option("--exclusive", "sharing", flag_value="exclusive", default=True)
@click.option("--stomp", "sharing", flag_value="stomp")
def term_command(
    port_baud: tuple[str, ...],
    plain: bool = False,
    reconnect: bool = False,
    scan_time: float = 0.0,
    sharing: ok_serial.SerialSharingType = "exclusive",
):
    """Start an interactive terminal on a serial port"""

    baud = 115200
    if port_baud[-1].isdigit():
        port_baud, baud = port_baud[:-1], int(port_baud[-1])

    match = " ".join(port_baud)
    copts = ok_serial.SerialConnectionOptions(baud=baud, sharing=sharing)
    mopts = ok_serial.SerialMonitorOptions(
        scan_timeout=scan_time,
        reconnect_limit=None if reconnect else 0,
    )
    topts = SerialTerminalOptions(
        match=match, copts=copts, mopts=mopts, plain=plain
    )
    run_terminal(topts)


def format_line(port: ok_serial.SerialPort):
    main_keys = "device tid subsystem vid_pid description serial_number".split()
    words = []
    for k in main_keys:
        if v := format_value(port, k):
            words.append(v)

    if age := format_age(port):
        words.append(age)

    return " ".join(words)


def format_detail(port: ok_serial.SerialPort) -> str:
    label = f"Port: {format_value(port, 'device')}"
    if tid := format_value(port, "tid"):
        label += f" {tid}"
    if age := format_age(port):
        label += f" {age}"
    return label + "".join(
        f"\n  {k}={format_value(port, k)}" for k in port.attr
    )


def format_value(port: ok_serial.SerialPort, k: str) -> str:
    if v := port.attr.get(k, ""):
        return repr(v) if re.search(r"\s", v) else v
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
