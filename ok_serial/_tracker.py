import contextlib
import logging
import msgspec
import typeguard

from ok_serial import _connection
from ok_serial import _scanning

log = logging.getLogger("ok_serial.tracker")


class TrackerOptions(msgspec.Struct, forbid_unknown_fields=True):
    match: _scanning.SerialPortMatcher
    poll_interval: float = 1.0  # seconds


@typeguard.typechecked
class SerialTracker(contextlib.AbstractContextManager):
    def __init__(
        self,
        topts: str | TrackerOptions,
        copts: int | _connection.SerialOptions = _connection.SerialOptions(),
    ):
        self._topts = (
            TrackerOptions(_scanning.SerialPortMatcher(topts))
            if isinstance(topts, str)
            else topts
        )
