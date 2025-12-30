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
workhorse [serial port](https://en.wikipedia.org/wiki/Serial_port) /
[UART](https://en.wikipedia.org/wiki/Universal_asynchronous_receiver-transmitter)
library for Python. It runs most places Python does and abstracts
lots of gnarly system details. However, some problems keep coming up:

- Most modern serial ports are USB, and USB serial ports get temporary
  device names like `/dev/ttyACM3` or `COM4`. Solutions like pyserial's
  [`serial.tools.list_ports.grep(...)`](https://pythonhosted.org/pyserial/tools.html#serial.tools.list_ports.grep)
  or Linux's
  [udev rules](https://dev.to/enbis/how-udev-rules-can-help-us-to-recognize-a-usb-to-serial-device-over-dev-tty-interface-pbk)
  require extra clumsy steps to use.

- Nonblocking or concurrent I/O with PySerial is perilous and often
  [broken](https://github.com/pyserial/pyserial/issues/281)
  [entirely](https://github.com/pyserial/pyserial/issues/280).

- Buffers in PySerial are small and unspecified; overruns cause lost data
  and/or unexpected blocking.

- PySerial doesn't lock ports by default; even when enabled, PySerial only
  uses one advisory locking method. Bad things happen when multiple programs
  try to use the same port.

The `ok-serial` library uses PySerial internally but has an improved interface:

- Ports are referenced by
  [port attribute match expressions](#serial-port-identification) with wildcard
  support, eg. `*RP2040*` or `2e43:0226` or `manufacturer="Arduino"`.
  (You can also use device path if desired.)

- I/O operations are thread safe and can be blocking, non-blocking,
  timeout, or async. Blocking operations can be interrupted.
  Semantics are well described, including concurrent access, partial
  reads/writes, interruption, I/O errors, and other edge cases.

- I/O buffers are limited only by system memory; writes never block.
  (Blocking drain operations are available to wait for output completion.)

- [Multiple port locking modes](#sharing-modes) are supported, with exclusive
  locking as the default. _All_ of
  [`/var/lock/LCK..*` files](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/ch05s09.html),
  [`flock(...)`](https://linux.die.net/man/2/flock) (like PySerial),
  and [`TIOCEXCL`](https://man7.org/linux/man-pages/man2/TIOCEXCL.2const.html)
  (as available) are used avoid contention.

- Offers a `SerialPortTracker` helper to wait for a device of interest to
  appear and rescan as needed after disconnection, to gracefully handle
  pluggable devices.

## Installation

```bash
pip install ok-serial
```

(or `uv add ok-serial`, etc.)

## Serial port identification

To better support USB serial devices, `ok-serial` identifies ports by
attributes like product description, USB vendor/product ID, and/or device
serial number rather than relying on transient device names like
`/dev/ttyACM3` or `COM4`.

To see port attributes, install `ok-serial`, connect your device(s) and run
`okserial --verbose` to list available in this format:

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

Attribute definitions are somewhat platform dependent, but `device`,
`name`, `description`, `hwid`, and (for USB) `vid`, `pid`, `serial_number`,
`location`, `manufacturer`, `product` and `interface` are semi-standardized.

To select ports to use, `ok-serial` uses **port match expression** strings,
which contain space-separated search terms:

- `word` - case INsensitive whole-word match in any attribute value
- `wild*word?` - `*` and `?` are wildcards (any text, single character)
- `spaces\ and\ st\*rs` - special characters can be escaped with backslash...
- `"spaces and st*rs"` - ...or with C/JS/Python-style quoted strings
- `attr="specific value"` - scoped to attribute prefix, must match whole value
- `~/regexp/` - case SENSITIVE regex match
  ([Python `re`](https://docs.python.org/3/library/re.html))
- `attr~/regexp/` - regex match can also be attribute scoped (partial match)
- `attr~/^regexp$/` - use regex anchors to match the whole attribute value

Some examples:

- `Pico Serial` - the words `pico` AND `serial` must each appear somewhere
  (any case, as a whole word)
- `RP2040 DF625*` - the word `rp2040` AND a word starting with `df625`
- `subsys="usb"` - `subsystem` must equal `usb` (any case but whole value)
- `Adafruit serial~/^DF625/` - `adafruit` must appear somewhere (any
case), and `serial_number` must begin with `DF625` (uppercase as written)

You can pass a match expression to `okserial` and set
`$OK_LOGGING_LEVEL=debug` to see parsing results:

```text
% OK_LOGGING_LEVEL=debug okserial -v 'Adafruit serial~/^DF625.*/'
🕸  ok_serial.scanning: Parsed 'Adafruit serial~/^DF625.*/':
  *~/(?<!\w)(Adafruit)(?!\w)/
  serial~/^DF625.*/
🕸  ok_serial.scanning: Found 36 ports
🎯 36 serial ports found, 1 matches 'Adafruit serial~/^DF625.*/'
Serial port: /dev/ttyACM3
   device='/dev/ttyACM3'
   name='ttyACM3'
   description='Feather RP2040 RFM - Pico Serial'
   hwid='USB VID:PID=239A:812D SER=DF62585783553434 LOCATION=3-2.1:1.0'
   vid='9114'
   pid='33069'
✅ serial_number='DF62585783553434'
   location='3-2.1:1.0'
✅ manufacturer='Adafruit'
   product='Feather RP2040 RFM'
   interface='Pico Serial'
   usb_device_path='/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1'
   device_path='/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1/3-2.1:1.0'
   subsystem='usb'
   usb_interface_path='/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1/3-2.1:1.0'
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
  is in use, _the program using the port is killed_ if possible.
  The port is opened regardless and all available locks are taken.

The implementation of these modes is limited by OS capabilities, process
permissions, and the historical conventions of port usage coordination.
Best efforts are taken but your mileage may vary.
