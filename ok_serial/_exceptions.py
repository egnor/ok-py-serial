"""Exception hierarchy for ok_serial"""


class OkSerialException(OSError):
    def __init__(
        self,
        message: str,
        device: str | None = None,
    ):
        super().__init__(f"{device}: {message}" if device else message)
        self.device = device


class SerialIoException(OkSerialException):
    pass


class SerialIoClosed(SerialIoException):
    pass


class SerialOpenException(OkSerialException):
    pass


class SerialOpenBusy(SerialOpenException):
    pass


class SerialScanException(OkSerialException):
    pass


class SerialMatcherInvalid(ValueError):
    pass
