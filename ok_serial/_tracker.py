import contextlib
import logging

import pydantic

from ok_serial import _connection
from ok_serial import _scanning

log = logging.getLogger("ok_serial.tracker")


class TrackerOptions(pydantic.BaseModel):
    match: _scanning.SerialPortMatcher
    poll_interval: float = 1.0  # seconds


class SerialTracker(contextlib.AbstractContextManager):
    @pydantic.validate_call(config={"arbitrary_types_allowed": True})
    def __init__(
        self,
        topts: str | TrackerOptions,
        copts: int | _connection.SerialOptions = _connection.SerialOptions(),
    ):
        self._topts = (
            TrackerOptions(match=_scanning.SerialPortMatcher(topts))
            if isinstance(topts, str)
            else topts
        )
