import blessed
import ok_serial


def main(tracker: ok_serial.SerialPortTracker, baud: int, wait: float):
    term = blessed.Terminal()
    print(term.width, term.height)
