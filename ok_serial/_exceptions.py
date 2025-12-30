"""Exception hierarchy for ok_serial"""


class SerialException(OSError):
    def __init__(
        self,
        message: str,
        port: str | None = None,
    ):
        super().__init__(f"{port}: {message}" if port else message)
        self.port = port


class SerialIoException(SerialException):
    pass


class SerialIoClosed(SerialIoException):
    pass


class SerialOpenException(SerialException):
    pass


class SerialOpenBusy(SerialOpenException):
    pass


class SerialScanException(SerialException):
    pass


class SerialMatcherInvalid(ValueError):
    pass
