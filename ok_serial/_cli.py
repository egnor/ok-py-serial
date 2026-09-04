#!/usr/bin/env python3

"""CLI tool to list serial ports on the system"""

import datetime
import logging
import re
import time

import click
import ok_logging_setup

import ok_serial

ok_logging_setup.skip_traceback_for(OSError)  # includes SerialException
ok_logging_setup.skip_traceback_for(EOFError)
ok_logging_setup.install()

MAIN_ATTRS = [
    "device",
    "tid",
    "subsystem",
    "vid_pid",
    "model",
    "description",
    "serial_number",
]


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
@click.option(
    "--uf2",
    "uf2_spec",
    metavar="MATCH",
    is_flag=False,
    flag_value="",
    help="Scan for UF2 bootloaders",
)
@click.option(
    "--scan-time", "-s", default=0.0, help="Seconds to wait for a matching port"
)
def main(
    match: tuple[str, ...],
    name_only: bool,
    one: bool,
    uf2_spec: str | None,
    verbose: bool,
    scan_time: float,
):
    """Print a list of available serial ports"""

    port_spec = " ".join(match) if (match or uf2_spec is None) else None
    port_list, port_n = [], 0
    uf2_list, uf2_n = [], 0

    start_time = time.time()
    while True:
        if port_spec is not None:
            port_n = len(port_list := ok_serial.scan_serial_ports(port_spec))
        if uf2_spec is not None:
            uf2_n = len(uf2_list := ok_serial.scan_uf2_devices(uf2_spec))

        if port_n or uf2_n:
            break

        remaining = start_time + scan_time - time.time()
        if remaining <= 0.0:
            break

        noun = "devices" if uf2_spec is not None else "ports"
        phr = f"matching {noun}" if (port_spec or uf2_spec) else f"{noun} found"
        logging.info(f"🔎 No {phr}, scanning... ({remaining:.1f}s)")
        time.sleep(0.5)

    if one and port_n + uf2_n > 1:
        noun = "devices" if uf2_n else "ports"
        message = f"Only --one allowed but multiple {noun} found:"
        if port_n:
            message += f"\n  🔌 Ports: {', '.join(p.name for p in port_list)}"
        if uf2_n:
            message += f"\n  💿 UF2: {', '.join(u.name for u in uf2_list)}"
        ok_logging_setup.exit(message, code=2)

    f = format_name if name_only else format_detail if verbose else format_line

    if port_n:
        logging.info(
            f"🔌 {port_n} serial port{'' if port_n == 1 else 's'} "
            + (f"matching {port_spec!r}" if port_spec else "found")
        )
        for port in port_list:
            click.echo(f(port))
    elif port_spec:
        logging.error(f"🚫 No serial ports matching {port_spec!r}")
    elif port_spec is not None:
        logging.error("❌ No serial ports found")

    if uf2_n:
        logging.info(
            f"💿 {uf2_n} UF2 bootloader{'' if uf2_n == 1 else 's'} "
            + (f"matching {uf2_spec!r}" if uf2_spec else "found")
        )
        for dev in uf2_list:
            click.echo(f(dev))
    elif uf2_spec:
        logging.error(f"🚫 No UF2 bootloaders matching {uf2_spec!r}")
    elif uf2_spec is not None:
        logging.error("❌ No UF2 bootloaders found")

    if not (port_n or uf2_n):
        raise SystemExit(1)


def format_name(port: ok_serial.PortInfo) -> str:
    return port.name


def format_line(port: ok_serial.PortInfo):
    words = []
    for k in MAIN_ATTRS:
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
    attrs = "".join(f"\n  {k}={format_value(port, k)}" for k in port.attr)
    return label + attrs + "\n"


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
