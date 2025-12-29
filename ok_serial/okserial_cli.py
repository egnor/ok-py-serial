#!/usr/bin/env python3

"""CLI tool to scan serial ports and/or communicate with them"""

import argparse
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
    )

    args = parser.parse_args()
    ok_logging_setup.install(
        {"OK_LOGGING_LEVEL": "warning" if args.list else "info"}
    )

    match = " ".join(args.match)
    tracker = ok_serial.SerialPortTracker(match)
    found = tracker.find_sync(timeout=args.wait)
    if not found:
        ok_logging_setup.exit("❌ No serial ports found")

    if args.one:
        if not args.verbose:
            args.list = True
        if (nm := len(found)) > 1:
            ok_logging_setup.exit(
                f"{nm} serial ports, only --one allowed:"
                + "".join(f"\n  {format_oneline(p, match)}" for p in found)
            )

    for port in found:
        if args.verbose:
            print(format_verbose(port, match), end="\n\n")
        elif args.list:
            print(port.name)
        else:
            print(format_oneline(port, match))


def format_oneline(port: ok_serial.SerialPort, match: str):
    matcher = ok_serial.SerialPortMatcher(match)
    hits = {k: True for k in matcher.matching_attrs(port)}
    sub, ser, desc = (
        f"{port.attr[k]}✅" if hits.pop(k, False) else port.attr.get(k, "")
        for k in "subsystem serial_number description".split()
    )

    nhits = [k for k in list(hits) if port.attr[k] in port.name and hits.pop(k)]
    words = [f"{port.name}✅" if nhits else port.name, sub]

    try:
        vid_int, pid_int = int(port.attr["vid"], 0), int(port.attr["pid"], 0)
    except (KeyError, ValueError):
        pass
    else:
        vp_hit = hits.pop("vid", False) + hits.pop("pid", False)
        words.append(f"{vid_int:04x}:{pid_int:04x}{'✅' if vp_hit else ''}")

    words.extend((ser, desc))
    words.extend(f"{k}={v!r}✅" for k, v in ((k, port.attr[k]) for k in hits))
    return " ".join(w for w in words if w)


def format_verbose(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher | None
):
    hits = matcher.matching_attrs(port) if matcher else set()
    return f"Serial port: {port.name}" + "".join(
        f"\n✅ {k}={v!r}" if k in hits else f"\n   {k}={v!r}"
        for k, v in port.attr.items()
    )


if __name__ == "__main__":
    main()
