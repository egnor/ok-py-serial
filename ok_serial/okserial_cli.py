#!/usr/bin/env python3

"""CLI tool to scan serial ports and/or communicate with them"""

import argparse
import datetime
import logging
import ok_logging_setup
import ok_serial

ok_logging_setup.skip_traceback_for(ok_serial.SerialMatcherInvalid)
ok_logging_setup.skip_traceback_for(ok_serial.SerialScanException)


def main():
    parser = argparse.ArgumentParser(description="Fuss with serial ports.")
    parser.add_argument("match", nargs="*", help="Properties to search for")
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="Print a one-line description of each port",
    )
    parser.add_argument(
        "--name",
        "-n",
        action="store_true",
        help="Print a simple list of port device names",
    )
    parser.add_argument(
        "--one",
        "-1",
        action="store_true",
        help="Fail unless exactly one port matches (implies -n unless -v)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print detailed properties of each port",
    )
    parser.add_argument(
        "--wait",
        "-w",
        default=0.0,
        help="Wait this many seconds for a matching port",
        type=float,
    )

    args = parser.parse_args()
    ok_logging_setup.install(
        {"OK_LOGGING_LEVEL": "warning" if args.name else "info"}
    )

    if args.one and not (args.list or args.verbose):
        args.name = True
    if not (args.list or args.name or args.verbose):
        args.list = True

    matcher = ok_serial.SerialPortMatcher(" ".join(args.match))
    tracker = ok_serial.SerialPortTracker(matcher)
    if matcher and args.wait:
        logging.info(
            "🔎 Finding serial ports (%.2fs timeout): %r",
            args.wait,
            str(matcher),
        )
    elif matcher:
        logging.info("🔎 Finding serial ports: %r", str(matcher))
    elif args.wait:
        logging.info("🔎 Finding serial ports (%.2fs timeout)", args.wait)
    else:
        logging.info("🔎 Finding serial ports...")

    found = tracker.find_sync(timeout=args.wait)
    num = len(found)
    if num == 0:
        if matcher:
            ok_logging_setup.exit(f"🚫 No serial ports match {str(matcher)!r}")
        else:
            ok_logging_setup.exit("❌ No serial ports found")

    if args.one and num != 1:
        ok_logging_setup.exit(
            f"{num} serial ports found, only --one allowed:"
            + "".join(f"\n  {format_oneline(p, matcher)}" for p in found)
        )

    logging.info("🔌 %d serial port%s found", num, "" if num == 1 else "s")

    if args.list:
        for port in found:
            print(format_oneline(port, matcher))
    elif args.name:
        for port in found:
            print(port.name)
    if args.verbose:
        for port in found:
            print(format_detail(port, matcher), end="\n\n")


def format_oneline(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher
):
    hit = matcher.hits(port)
    hit -= {"name"} if "device" in hit else set()

    out = ""
    for k in "device vid_pid subsystem serial_number description".split():
        if v := port.attr.get(k, ""):
            out += (repr(v) if " " in v else v) + ("✅ " if k in hit else " ")
            hit.discard(k)
        if k == "device":
            out += format_age(port) + " "

    for k, v in port.attr.items():
        out += f"{k}={v!r}✅ " if k in hit else ""

    return out.strip()


def format_detail(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher
):
    hit = matcher.hits(port)
    return f"Serial port: {port.name} {format_age(port)}".strip() + "".join(
        f"\n✅ {k}={v!r}" if k in hit else f"\n   {k}={v!r}"
        for k, v in port.attr.items()
    )


def format_age(port: ok_serial.SerialPort):
    try:
        dt = datetime.datetime.fromisoformat(port.attr.get("time", ""))
    except ValueError:
        return ""
    return format_timedelta(datetime.datetime.now() - dt)


def format_timedelta(d: datetime.timedelta):
    if d.days < 0:
        return f"-{format_timedelta(-d)}"
    h, m, s = d.seconds // 3600, (d.seconds % 3600) // 60, d.seconds % 60
    if d.days:
        return f"({d.days}d {h:02}:{m:02}:{s:02}s)"
    elif h:
        return f"({h}:{m:02}:{s:02}s)"
    elif m:
        return f"({m}:{s:02}s)"
    else:
        return f"({d.seconds + d.microseconds * 1e-6:.2f}s)"


if __name__ == "__main__":
    main()
