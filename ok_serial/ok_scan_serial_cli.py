#!/usr/bin/env python3

"""CLI tool to scan serial ports"""

import argparse
import ok_logging_setup
import ok_serial
import re

ok_logging_setup.install()


def main():
    parser = argparse.ArgumentParser(description="Scan and list serial ports.")
    parser.add_argument("match", nargs="?", help="Properties to search for")
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="Print a simple list of device names",
    )
    parser.add_argument(
        "--one",
        "-1",
        action="store_true",
        help="Fail unless exactly one port matches (implies -l unless -v)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print detailed properties of each port",
    )

    args = parser.parse_args()
    ports = ok_serial.scan_serial_ports(args.match)
    match_text = f" matching {args.match!r}" if args.match else ""
    if args.one:
        if args.verbose:
            args.list = True
        if not ports:
            ok_logging_setup.exit(f"No serial ports found{match_text}")
        if len(ports) > 1:
            ok_logging_setup.exit(
                f"{len(ports)} serial ports found{match_text}:"
                + "".join(f"\n  {format_standard(p)}" for p in ports)
            )

    for port in ports:
        if args.verbose:
            print(format_verbose(port), end="\n\n")
        elif args.list:
            print(port.name)
        else:
            print(format_standard(port))


UNQUOTED_RE = re.compile(r'[^:"\s\\]*')


def format_standard(port: ok_serial.SerialPort):
    line = port.name
    if sub := port.attr.get("subsystem"):
        line += f" {sub}"
    try:
        vid_int, pid_int = int(port.attr["vid"], 0), int(port.attr["pid"], 0)
    except (KeyError, ValueError):
        pass
    else:
        line += f" {vid_int:04x}:{pid_int:04x}"
    if ser := port.attr.get("serial_number"):
        line += f" {ser}"
    if desc := port.attr.get("description"):
        line += f" {desc}"
    return line


def format_verbose(port: ok_serial.SerialPort):
    return port.name + "".join(
        f"\n  {k}: {repr(v)}" for k, v in port.attr.items()
    )


if __name__ == "__main__":
    main()
