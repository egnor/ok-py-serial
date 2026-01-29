import blessed
import ok_serial


def run_terminal(match: str, baud: int, wait_time: float):
    tracker = ok_serial.SerialPortTracker(match=match, baud=baud)
    tracker.connect_sync(timeout=wait_time)
    term = blessed.Terminal()
    print(term.width, term.height)
