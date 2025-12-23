#!/usr/bin/env python3

"""CLI tool to scan serial ports"""

import argparse
import logging
import ok_logging_setup
import ok_serial

ok_logging_setup.install()
ok_logging_setup.skip_traceback_for(ok_serial.SerialMatcherInvalid)
ok_logging_setup.skip_traceback_for(ok_serial.SerialScanException)

logger = logging.getLogger("ok_serial_scan")


def main():
    parser = argparse.ArgumentParser(description="Scan and list serial ports.")
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

    args = parser.parse_args()
    match = " ".join(args.match)
    matcher = ok_serial.SerialPortMatcher(match) if match else None
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


def format_oneline(
    port: ok_serial.SerialPort, matcher: ok_serial.SerialPortMatcher | None
):
    hits = {k: True for k in matcher.matching_attrs(port)} if matcher else {}
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
