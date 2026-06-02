import ok_serial

import logging


def run_terminal(tracker: ok_serial.SerialPortTracker):
    """Runs an interactive terminal communicating with the serial tracker."""

    while True:
        logging.info("🔎 Scanning for serial ports: %r", tracker.match)
        tracker.connect_sync()
