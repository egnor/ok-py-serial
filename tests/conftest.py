import contextlib
import io
import ok_logging_setup
import os
import pty
import pytest
import typing

ok_logging_setup.install(
    {
        "OK_LOGGING_LEVEL": "ok_serial=DEBUG,WARNING",
        "OK_LOGGING_OUTPUT": "stdout",
    }
)


class PseudoTtySerial(typing.NamedTuple):
    path: str
    control: io.FileIO
    simulated: io.FileIO


@pytest.fixture
def pty_serial():
    with contextlib.ExitStack() as cleanup:
        ctrl_fd, sim_fd = pty.openpty()
        path = os.ttyname(sim_fd)
        ctrl = cleanup.enter_context(os.fdopen(ctrl_fd, "r+b", buffering=0))
        sim = cleanup.enter_context(os.fdopen(sim_fd, "r+b", buffering=0))
        yield PseudoTtySerial(path=path, control=ctrl, simulated=sim)
