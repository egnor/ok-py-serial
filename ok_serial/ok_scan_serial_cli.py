#!/usr/bin/env python3

"""CLI tool to scan serial ports"""

import argparse
import logging
import ok_logging_setup
import ok_serial

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
    print(ports)


if __name__ == "__main__":
    main()
