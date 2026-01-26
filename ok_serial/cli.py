#!/usr/bin/env python3

"""CLI tool to scan serial ports and/or communicate with them"""

import argparse
import datetime
import logging
import ok_logging_setup
import ok_serial
import ok_serial.terminal
import re

ok_logging_setup.skip_traceback_for(ok_serial.SerialMatcherInvalid)
ok_logging_setup.skip_traceback_for(ok_serial.SerialScanException)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(title="actions", dest="command")
    list_parser = subparsers.add_parser("list", help="List known serial ports")
    list_parser.add_argument("port", nargs="*", help="port match expression")
    list_parser.add_argument(
        "--one",
        "-1",
        action="store_true",
        help="require exactly one port (implies -n unless -v)",
    )
    list_parser.add_argument(
        "--wait", "-w", default=0.0, help="seconds to scan", type=float
    )
    list_style_group = list_parser.add_mutually_exclusive_group()
    list_style_group.add_argument(
        "--name", "-n", action="store_true", help="print device file only"
    )
    list_style_group.add_argument(
        "--verbose", "-v", action="store_true", help="print detailed properties"
    )

    term_parser = subparsers.add_parser("term", help="Terminal emulator")
    term_parser.add_argument("port", nargs="+", help="port match expression")
    term_parser.add_argument("baud", type=int, help="baud rate")
    term_parser.add_argument(
        "--wait", "-w", default=0.0, help="seconds to scan", type=float
    )

    args = parser.parse_args()
    if not args.command:
        args = parser.parse_args(["list"])
    if args.command == "list" and args.one and not args.verbose:
        args.name = True

    level = "warning" if args.command == "list" and args.name else "info"
    ok_logging_setup.install({"OK_LOGGING_LEVEL": level})

    if args.command in ("list", "term"):
        expr = " ".join(args.port)
        tracker = ok_serial.SerialPortTracker(match=expr)
        if expr and args.wait:
            logging.info(
                "🔎 Finding serial ports (%.2fs timeout): %r",
                args.wait,
                expr,
            )
        elif expr:
            logging.info("🔎 Finding serial ports: %r", expr)
        elif args.wait:
            logging.info("🔎 Finding serial ports (%.2fs timeout)", args.wait)
        else:
            logging.info("🔎 Finding serial ports...")

        found = tracker.find_sync(timeout=args.wait)
        num = len(found)
        if num == 0:
            if expr:
                ok_logging_setup.exit(f"🚫 No serial ports match {expr!r}")
            else:
                ok_logging_setup.exit("❌ No serial ports found")

        logging.info("🔌 %d serial port%s found", num, "" if num == 1 else "s")

    if args.command == "list":
        matcher = tracker.matcher
        if args.one and num != 1:
            ok_logging_setup.exit(
                f"{num} serial ports found, only --one allowed:"
                + "".join(f"\n  {format_line(p, matcher)}" for p in found)
            )
        if args.name:
            for port in found:
                print(port.name)
        elif args.verbose:
            for port in found:
                print(format_detail(port, matcher), end="\n\n")
        else:
            for port in found:
                print(format_line(port, matcher))

    if args.command == "term":
        ok_serial.terminal.main(tracker=tracker, baud=args.baud, wait=args.wait)


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
