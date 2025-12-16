import contextlib
import logging

import pydantic

from ok_serial import _connection
from ok_serial import _scanning

log = logging.getLogger("ok_serial.tracker")


class TrackerOptions(pydantic.BaseModel):
    match: _scanning.SerialPortMatcher
    poll_seconds: float = 0.5


class SerialTracker(contextlib.AbstractContextManager):
    @pydantic.validate_call(config={"arbitrary_types_allowed": True})
    def __init__(
        self,
        topts: str | TrackerOptions,
        copts: int | _connection.SerialOptions = _connection.SerialOptions(),
    ):
        if isinstance(topts, str):
            topts = TrackerOptions(match=_scanning.SerialPortMatcher(topts))

        self._tracker_opts = topts
        self._connection_opts = copts
