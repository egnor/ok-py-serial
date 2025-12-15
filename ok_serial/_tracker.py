import contextlib
import logging
import typeguard

# from ok_serial import _connection
# from ok_serial import _scanning

log = logging.getLogger("ok_serial.tracker")


@typeguard.typechecked
class SerialDeviceTracker(contextlib.AbstractContextManager):
    def __init__(self):
        pass
