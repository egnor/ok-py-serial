"""Exception hierarchy for ok_serial"""


class OkSerialException(OSError):
    def __init__(
        self,
        message: str,
        port: str | None = None,
    ):
        super().__init__(f"{port}: {message}" if port else message)
        self.port = port


class SerialIoException(OkSerialException):
    pass


class SerialIoClosed(SerialIoException):
    pass


class SerialOpenException(OkSerialException):
    pass


class SerialOpenBusy(SerialOpenException):
    pass


class SerialMatcherException(ValueError):
    pass
