#!/usr/bin/env python3

"""CLI tool to scan serial ports"""

import argparse
import logging
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
    matcher = ok_serial.SerialPortMatcher(args.match) if args.match else None
    found = ok_serial.scan_serial_ports()
    if not found:
        ok_logging_setup.exit("ok_serial_scan: No ports found")
    if not matcher:
        matching = found
        logging.info("ok_serial_scan: %d ports found", len(found))
    else:
        matching = [p for p in found if matcher.matches(p)]
        nf, nm, m = len(found), len(matching), str(matcher)
        if not matching:
            ok_logging_setup.exit(
                "ok_serial_scan: %d ports found, none match %r", nf, m
            )
        v = "matches" if nm == 1 else "match"
        logging.info("ok_serial_scan: %d ports found, %d %s %r", nf, nm, v, m)

    if args.one:
        if not args.verbose:
            args.list = True
        if (nm := len(matching)) > 1:
            ok_logging_setup.exit(
                f"ok_serial_scan: {nm} ports match, only --one allowed:"
                + "".join(f"\n  {format_standard(p)}" for p in matching)
            )

    for port in matching:
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
    return f"Serial port: {port.name}:" + "".join(
        f"\n  {k}: {repr(v)}" for k, v in port.attr.items()
    )


if __name__ == "__main__":
    main()
