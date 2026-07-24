import dataclasses
import collections.abc


@dataclasses.dataclass(frozen=True)
class SerialPort:
    """Metadata about a serial port found on the system"""

    name: str
    """The OS device identifier, eg. `/dev/ttyUSB3`, 'COM4', etc."""

    attr: dict[str, str]
    """
    [Attributes](https://github.com/egnor/py-ok-serial#serial-port-attributes)
    """

    def __str__(self):
        return self.name


PortPredicate = collections.abc.Callable[[SerialPort], bool]
"""A function that returns True for ports of interest."""
