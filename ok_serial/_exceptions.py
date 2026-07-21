"""Exception hierarchy for ok_serial"""


class SerialException(OSError):
    """Exception base class for `okserial` I/O errors."""

    port: str | None
    """The device name of the serial port involved in the error."""

    def __init__(self, message: str, port: str | None = None):
        super().__init__(f"{port}: {message}" if port else message)
        self.port = port


class SerialIoException(SerialException):
    """Exception raised for I/O errors communicating with serial ports."""

    pass


class SerialIoClosed(SerialIoException):
    """Exception raised when I/O is attempted on a closed serial port."""

    pass


class SerialIoConflict(SerialIoException):
    """Exception raised when a `polite` connection detects another user."""

    pass


class SerialIoUnsupported(SerialIoException):
    """Exception raised for an operation not implemented by the serial port."""

    pass


class SerialOpenException(SerialIoException):
    """Exception raised for system errors opening a serial port."""

    pass


class SerialOpenBusy(SerialOpenException):
    """Exception raised if an open attempt fails due to port contention."""

    pass


class SerialScanException(SerialException):
    """Exception raised for system errors scanning available ports."""

    pass


class SerialMonitorExhausted(SerialException):
    """Exception raised for permanent timeout or retry limit finding a port."""

    pass
