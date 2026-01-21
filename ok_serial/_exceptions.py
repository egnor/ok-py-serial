"""Exception hierarchy for ok_serial"""


class SerialException(OSError):
    """Base class for system errors encountered in `okserial` operations."""

    def __init__(
        self,
        message: str,
        port: str | None = None,
    ):
        super().__init__(f"{port}: {message}" if port else message)
        self.port = port


class SerialIoException(SerialException):
    """Exception raised for I/O errors communicating with serial ports."""

    pass


class SerialIoClosed(SerialIoException):
    """Exception raised when I/O is attempted on a closed serial port."""

    pass


class SerialOpenException(SerialException):
    """Exception raised for system errors opening a serial port."""

    pass


class SerialOpenBusy(SerialOpenException):
    """Exception raised if an open attempt failes due to port contention."""

    pass


class SerialScanException(SerialException):
    """Exception raised for system errors scanning available ports."""

    pass


class SerialMatcherInvalid(ValueError):
    """Exception raised when a port matcher string is syntactically invalid."""

    pass
