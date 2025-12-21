# ok-serial for Python

Python serial port I/O ([wrapping PySerial](https://www.pyserial.com/)) with
improved port discovery, concurrency, and non-blocking semantics.

Think twice before using this library! Consider something more established:

- [good old PySerial](https://www.pyserial.com/) - it is _very_ well established
- [pyserial-asyncio](https://github.com/pyserial/pyserial-asyncio) - official
  and "proper" [asyncio](https://docs.python.org/3/library/asyncio.html)
  support for PySerial
- [pyserial-asyncio-fast](https://github.com/home-assistant-libs/pyserial-asyncio-fast)
  \- a fork of pyserial-asyncio designed for more efficient writes
- [aioserial](https://github.com/mrjohannchang/aioserial.py) - alternative
  asyncio wrapper designed for ease of use

## Purpose

Since 2001, [PySerial](https://www.pyserial.com/) has been the
workhorse [serial port](https://en.wikipedia.org/wiki/Serial_port)
([UART](https://en.wikipedia.org/wiki/Universal_asynchronous_receiver-transmitter))
library for Python. It runs on most Python platforms and abstracts
lots of gnarly system details. However, certain problems keep coming up:

- Most serial ports are USB, and USB serial ports get assigned cryptic
  temporary names like `/dev/ttyACM3` or `COM4`. Using
  [`serial.tools.list_ports.grep(...)`](https://pythonhosted.org/pyserial/tools.html#serial.tools.list_ports.grep)
  is a clumsy multi step process; linux supports
  [udev rules](https://dev.to/enbis/how-udev-rules-can-help-us-to-recognize-a-usb-to-serial-device-over-dev-tty-interface-pbk)
  but they're not exactly user friendly.

- Nonblocking or concurrent I/O with PySerial is perilous and often
  [broken](https://github.com/pyserial/pyserial/issues/281)
  [entirely](https://github.com/pyserial/pyserial/issues/280).

- Buffer sizes are finite and unspecified; overruns cause lost data
  and/or blocking.

- Port locking is off by default in PySerial; even if enabled, it only
  uses one advisory locking method. Bad things happen if multiple programs
  try to use the same port.
