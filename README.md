# ok-serial for Python &nbsp; 🔌〡〇〡〇〡🐍

Python serial I/O library [based on PySerial](https://www.pyserial.com/) with
improved discovery and interface semantics.

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
  is a clumsy multi step process; linux allows
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

- Ports are referenced by
  [string expressions](#identifying-ports) that can match attributes
  with wildcard support, eg. `desc:Arduino*` or `2e43:0226` or `*RP2040*`.
  (You can also specify exact device path if desired.)

- I/O operations are thread safe and can be blocking, non-blocking,
  timeout, or async. All blocking operations can be interrupted.
  Semantics are well described, including concurrent access, partial
  reads/writes, errors, and other edge cases.

- I/O buffers are unlimited except for system memory; writes
  never block. (You can use a blocking drain operation to wait for
  output completion if desired.)

- Includes [multiple port locking modes](#sharing-modes) with exclusive locking
  as the default. Employs _all_ of
  [`/var/lock/LCK..*` files](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/ch05s09.html),
  [`flock(...)`](https://linux.die.net/man/2/flock) (like PySerial),
  and [`TIOCEXCL`](https://man7.org/linux/man-pages/man2/TIOCEXCL.2const.html)
  (as available) to avoid contention.

- Includes a `SerialTracker` helper to wait for a device of interest to
  appear, rescanning as needed after disconnection, to handle devices
  that might get plugged and unplugged.

## Installation

```bash
pip install ok-serial
```

(or `uv add ok-serial`, etc.)

## Identifying ports

Device names like `/dev/ttyUSB3` or `COM4` aren't very useful for USB
serial ports, so `ok-serial` uses **port match expressions** which
are strings that identify ports of interest by attributes such as the
device manufacturer (eg. `Adafruit`), product name (eg.
`CP2102 USB to UART Bridge Controller`), USB vendor/product ID (eg.
`239a:812d`), serial number, or other properties.

To see port attributes, install `ok-serial` and run
`ok_scan_serial --verbose` to list available ports like this:

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

Attribute names and value formats are inherited from PySerial and
the underlying OS and can vary, but
`device`, `name`, `description`, `hwid`, and (for USB) `vid`, `pid`,
`serial_number`, `location`, `manufacturer`, `product` and `interface`
are semi-standardized.

Match expressions can be a simple value, selecting any port with a
matching value (case-insensitive whole-string match):

```text
Pico Serial
```

Match values can include `*` and `?` wildcards:

```text
*RP2040*
```

Expressions can include a field selector (prefix abbreviation is OK):

```text
subsys:usb
```

Values containing colons, quotes, or special characters
should be quoted using Python/C/JS string escaping:

```text
location:"3-2.1:1.0"
```

Multiple constraints can be combined; all must match:

```text
manufacturer:Adafruit serial:DF625*
```

To experiment, pass a match expression to `ok_scan_serial` on the
command line; set `$OK_LOGGING_LEVEL=debug` to see the parse result:

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

## Sharing modes

When opening a port, `ok-serial` offers a choice of four sharing modes:

- `oblivious` - no locking is done and advisory locks are ignored. If
  multiple programs open the port, they will all send and receive data
  to the same device. This mode is not recommended.

- `polite` - locking is checked at open, and if the port is in use the
  open fails. Once opened, no locks are held except for a shared lock
  to discourage other `polite` users from opening the port. If a
  less polite program opens the port later there will be conflict.
  (In the future, this mode will attempt to notice such conflicts
  and close out the port, deferring to the less-polite program.)

- `exclusive` (the default mode) - locking is checked at open, and if the
  port is in use the open fails. Once opened, several means of locking
  are employed to prevent or discourage others from opening the port.

- `stomp` (use with care!) - locking is checked at open, and if the port
  is in use, _the program using the port is killed_ if permissions
  allow. The port is opened regardless of any other users and all
  available locks are taken.

The library's ability to implement these modes can be limited by
operating system capabilities, process permissions, and the variously
questionable historical conventions for port usage coordination.
