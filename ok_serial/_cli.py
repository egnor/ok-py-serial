#!/usr/bin/env python3

"""CLI tool to list serial ports on the system"""

import datetime
import logging
import re

import click
import ok_logging_setup

import ok_serial

ok_logging_setup.skip_traceback_for(OSError)  # includes SerialException
ok_logging_setup.skip_traceback_for(EOFError)
ok_logging_setup.install()


@click.command()
@click.argument("match", nargs=-1)
@click.option(
    "--one", "-1", is_flag=True, help="Fail unless exactly one port matches"
)
@click.option(
    "--name-only", "-n", is_flag=True, help="Print device names, one per line"
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Print details for each port"
)
def main(
    match: tuple[str, ...],
    name_only: bool = False,
    one: bool = False,
    verbose: bool = False,
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
    if name_only:
        for p in found:
            click.echo(p.name)
    elif verbose:
        for p in found:
            click.echo(format_detail(p) + "\n")
    else:
        for p in found:
            click.echo(format_line(p))


def format_line(port: ok_serial.PortInfo):
    main_keys = "device tid subsystem vid_pid description serial_number".split()
    words = []
    for k in main_keys:
        if v := format_value(port, k):
            words.append(v)

    if age := format_age(port):
        words.append(age)

    return " ".join(words)


def format_detail(port: ok_serial.PortInfo) -> str:
    label = f"Port: {format_value(port, 'device')}"
    if tid := format_value(port, "tid"):
        label += f" {tid}"
    if age := format_age(port):
        label += f" {age}"
    return label + "".join(
        f"\n  {k}={format_value(port, k)}" for k in port.attr
    )


def format_value(port: ok_serial.PortInfo, k: str) -> str:
    if v := port.attr.get(k, ""):
        return repr(v) if re.search(r"\s", v) else v
    return ""


def format_age(port: ok_serial.PortInfo) -> str:
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
