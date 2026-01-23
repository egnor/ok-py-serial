#!/usr/bin/env python3

"""CLI tool to scan serial ports and/or communicate with them"""

import argparse
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
    parser.add_argument(
        "--wait",
        "-w",
        default=0.0,
        help="Wait this many seconds for a matching port",
        type=float,
    )

    args = parser.parse_args()
    ok_logging_setup.install(
        {"OK_LOGGING_LEVEL": "warning" if args.list else "info"}
    )

    match = ok_serial.SerialPortMatcher(" ".join(args.match))
    tracker = ok_serial.SerialPortTracker(match)
    if match and args.wait:
        logging.info(
            "🔎 Finding serial ports (%.2fs timeout): %r", args.wait, str(match)
        )
    elif match:
        logging.info("🔎 Finding serial ports: %r", str(match))
    elif args.wait:
        logging.info("🔎 Finding serial ports (%.2fs timeout)", args.wait)
    else:
        logging.info("🔎 Finding serial ports...")

    found = tracker.find_sync(timeout=args.wait)
    num = len(found)
    if num == 0:
        if str(match):
            ok_logging_setup.exit(f"🚫 No serial ports match {str(match)!r}")
        else:
            ok_logging_setup.exit("❌ No serial ports found")

    if args.one and not args.verbose:
        args.list = True
    if args.one and num != 1:
        ok_logging_setup.exit(
            f"{num} serial ports found, only --one allowed:"
            + "".join(f"\n  {format_oneline(p, match)}" for p in found)
        )

    logging.info("🔌 %d serial port%s found", num, "" if num == 1 else "s")
    for port in found:
        if args.verbose:
            print(format_verbose(port, match), end="\n\n")
        elif args.list:
            print(port.name)
        else:
            print(format_oneline(port, match))


def format_oneline(
    port: ok_serial.SerialPort, match: ok_serial.SerialPortMatcher
):
    mark = {a: True for a in match.matching_attrs(port)}
    vidpid, sub, ser, desc = (
        f"{port.attr[k]}✅" if mark.pop(k, False) else port.attr.get(k, "")
        for k in "vid_pid subsystem serial_number description".split()
    )

    mname = [k for k in list(mark) if port.attr[k] in port.name and mark.pop(k)]
    words = [f"{port.name}✅" if mname else port.name, sub, vidpid, ser]

    words.append(desc and f"{desc!r}")
    words.extend(f"{k}={v!r}✅" for k, v in ((k, port.attr[k]) for k in mark))
    return " ".join(w for w in words if w)


def format_verbose(
    port: ok_serial.SerialPort, match: ok_serial.SerialPortMatcher
):
    hits = match.matching_attrs(port)
    return f"Serial port: {port.name}" + "".join(
        f"\n✅ {k}={v!r}" if k in hits else f"\n   {k}={v!r}"
        for k, v in port.attr.items()
    )


if __name__ == "__main__":
    main()
