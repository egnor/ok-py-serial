# ok-serial for Python

Python serial port I/O ([based on PySerial](https://www.pyserial.com/)) with
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
lots of gnarly system details. However, some problems keep coming up:

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

The `ok-serial` library uses PySerial internally but has its own consistent
interface to fix these problems and be generally smoove:

- Ports are referenced by a string expression that can match many properties
  with wildcard support, eg. `desc:Arduino*` or `2e43:0226` or `*RP2040*`.
  (See below; you can also specify exact device path if desired.)

- I/O operations are thread safe and can be blocking, non-blocking,
  timeout, or async. All blocking operations can be interrupted.
  Semantics are well described, including concurrent access, partial
  reads/writes, errors, and other edge cases.

- I/O buffers are unlimited except for system memory. Writes
  never block. (You can use a blocking drain operation to wait for
  output completion if desired.)

- Offers `oblivious`, `polite`, `exclusive`, and `stomp` port locking modes
  (see below), with `exclusive` as default. Uses _all_ of
  [`/var/lock/LCK..*` files](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/ch05s09.html),
  [`flock(...)`](https://linux.die.net/man/2/flock) (like PySerial),
  and [`TIOCEXCL`](https://man7.org/linux/man-pages/man2/TIOCEXCL.2const.html)
  (as available) to prevent contention.

- Offers a `SerialTracker` helper to wait for a device of interest to appear,
  rescanning after disconnection, to work with devices that might get
  plugged and unplugged.

## Installation

```bash
pip install ok-serial
```

(or `uv add ok-serial`, etc.)

## Serial port match expressions

Serial ports can be identified by their operating system device name
(like `/dev/ttyUSB3` or `COM4`), but are more usefully identified
by attributes such as their manufacturer (eg. `Adafruit`), product name
(eg. `CP2102 USB to UART Bridge Controller`), serial number, or other
properties (eg. USB vendor/product `239a:812d`).

After installing `ok-serial`, running `ok_scan_serial -v` shows the
known attributes of ports on your system, formatted like this:

```text
Serial port: /dev/ttyACM3
  device: '/dev/ttyACM3'
  name: 'ttyACM3'
  description: 'Feather RP2040 RFM - Pico Serial'
  hwid: 'USB VID:PID=239A:812D SER=DF62585783553434 LOCATION=3-2.1:1.0'
  vid: '9114'
  pid: '33069'
  serial_number: 'DF62585783553434'
  location: '3-2.1:1.0'
  manufacturer: 'Adafruit'
  product: 'Feather RP2040 RFM'
  interface: 'Pico Serial'
  usb_device_path: '/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1'
  device_path: '/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1/3-2.1:1.0'
  subsystem: 'usb'
  usb_interface_path: '/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1/3-2.1:1.0'
```

The specific attribute names and values are inherited from PySerial and
somewhat system-dependent, but
`device`, `name`, `description`, `hwid`, and (for USB) `vid`, `pid`,
`serial_number`, `location`, `manufacturer`, `product` and `interface`
are semi standardized.

In `ok-serial`, **match expressions** are string values that select ports
based on attributes. They can be simple strings to match any attribute
value exactly:

```text
Pico Serial
```

They can include `*` and `?` wildcards:

```text
*RP2040*
```

They can include a field selector, which may be abbreviated to a prefix:

```text
subsystem:usb
```

If the value to match contains colons, quotes, or special characters, they
should be quoted with Python/C/JS/JSON string quoting:

```text
location:"3-2.1:1.0"
```

Multiple constraints can be combined:

```text
manufacturer:Adafruit serial:DF625*
```

For experimentation, you can give a match expression to `ok_scan_serial`
on the command line; if you set `$OK_LOGGING_LEVEL=debug` you can see the
parsing result:

```text
% OK_LOGGING_LEVEL=debug ok_scan_serial -v 'manufacturer:Adafruit serial:DF625*'
🕸  ok_serial.scanning: Parsed 'manufacturer:Adafruit serial:DF625*':
  manufacturer: /(?s:Adafruit)\Z/
  serial: /(?s:DF625.*)\Z/
🕸  ok_serial.scanning: Found 36 ports
36 serial ports found, 1 matches 'manufacturer:Adafruit serial:DF625*'
Serial port: /dev/ttyACM3
  device: '/dev/ttyACM3'
  name: 'ttyACM3'
  description: 'Feather RP2040 RFM - Pico Serial'
  hwid: 'USB VID:PID=239A:812D SER=DF62585783553434 LOCATION=3-2.1:1.0'
  vid: '9114'
  pid: '33069'
  serial_number: 'DF62585783553434'
  location: '3-2.1:1.0'
  manufacturer: 'Adafruit'
  product: 'Feather RP2040 RFM'
  interface: 'Pico Serial'
  usb_device_path: '/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1'
  device_path: '/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1/3-2.1:1.0'
  subsystem: 'usb'
  usb_interface_path: '/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1/3-2.1:1.0'
```
