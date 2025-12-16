"""Exception hierarchy for ok_serial"""


class OkSerialException(OSError):
    def __init__(
        self,
        message: str,
        port: str | None = None,
    ):
        super().__init__(f"{port}: {message}" if port else message)
        self.port = port


class SerialIoFailed(OkSerialException):
    pass


class SerialIoClosed(SerialIoFailed):
    pass


class SerialOpenFailed(OkSerialException):
    pass


class SerialPortBusy(SerialOpenFailed):
    pass


class SerialMatcherParseFailed(ValueError):
    pass
