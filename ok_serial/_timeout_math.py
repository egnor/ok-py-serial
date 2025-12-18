import time
from threading import TIMEOUT_MAX


def to_deadline(timeout: float | int | None) -> float:
    if timeout is None or timeout >= TIMEOUT_MAX:
        return TIMEOUT_MAX
    elif timeout <= 0:
        return 0.0
    else:
        return min(TIMEOUT_MAX, time.monotonic() + timeout)


def from_deadline(deadline: float) -> float:
    if deadline >= TIMEOUT_MAX:
        return TIMEOUT_MAX
    elif deadline <= 0:
        return 0.0
    else:
        return max(0.0, deadline - time.monotonic())
