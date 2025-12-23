#!/usr/bin/env python3

"""CLI tool to scan serial ports"""

import argparse
import logging
import ok_logging_setup
import ok_serial
import re

ok_logging_setup.install()

logger = logging.getLogger("ok_serial_scan")


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
        logging.info("%d ports found", len(found))
    else:
        matching = [p for p in found if matcher.matches(p)]
        nf, nm, m = len(found), len(matching), str(matcher)
        if not matching:
            ok_logging_setup.exit("%d ports, none match %r", nf, m)
        v = "matches" if nm == 1 else "match"
        logging.info("%d ports, %d %s %r", nf, nm, v, m)

    if args.one:
        if not args.verbose:
            args.list = True
        if (nm := len(matching)) > 1:
            ok_logging_setup.exit(
                f"ok_serial_scan: {nm} ports, only --one allowed:"
                + "".join(f"\n  {format_oneline(p, matcher)}" for p in matching)
            )

    for port in matching:
        if args.verbose:
            print(format_verbose(port, matcher), end="\n\n")
        elif args.list:
            print(port.name)
        else:
            print(format_oneline(port, matcher))


UNQUOTED_RE = re.compile(r'[^:"\s\\]*')


XXX fix highlighting
def format_oneline(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher | None
):
    attr = dict(port.attr)
    line = attr.pop("device") or port.name
    for hit in (matcher.matching_attrs(port) if matcher else set()):
        line += f" *{hit}:{attr[hit][1:]!r}"
        del attr[hit]
        
    if sub := attr.get("subsystem"):
        line += f" {sub}"
    try:
        vid_int, pid_int = int(attr["vid"], 0), int(attr["pid"], 0)
    except (KeyError, ValueError):
        pass
    else:
        vid, pid = attr.pop("vid", ""), attr.pop("pid", "")
        starred = vid.startswith("*") or pid.startswith("*")
        line += f" {'*' if starred else ''}{vid_int:04x}:{pid_int:04x}"
    if ser := attr.pop("serial_number", None):
        line += f" {ser}"
    if desc := attr.pop("description", None):
        line += f" {desc}"
    return line


def format_verbose(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher | None
):
    return f"Serial port: {port.name}:" + "".join(
        f"\n  {k}: {repr(v)}" for k, v in port.attr.items()
    )


if __name__ == "__main__":
    main()
