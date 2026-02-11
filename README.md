# OK serial I/O for Python &nbsp; 🔌〡〇〡〇〡🐍

A Python serial port library
(based on [PySerial](https://www.pyserial.com/))
with improved port discovery and I/O semantics.
[(API reference)](https://egnor.github.io/ok-py-serial/)

Think twice before using this library! Consider something more established:

- [good old PySerial](https://www.pyserial.com/)
  \- the implementation under `ok-serial`, well established and widely used
- [pyserial-asyncio](https://github.com/pyserial/pyserial-asyncio)
  \- official and "proper"
  [asyncio](https://docs.python.org/3/library/asyncio.html)
  support for PySerial
- [pyserial-asyncio-fast](https://github.com/home-assistant-libs/pyserial-asyncio-fast)
  \- pyserial-asyncio fork designed for faster writes
- [aioserial](https://github.com/mrjohannchang/aioserial.py)
  \- alternative asyncio wrapper designed for ease of use
- bonus recommendation: [tio](https://github.com/tio/tio)
  \- not a library, not Python, but a great serial terminal utility

## Purpose

Since 2001, [PySerial](https://www.pyserial.com/) has been the
workhorse [serial port](https://en.wikipedia.org/wiki/Serial_port) /
[UART](https://en.wikipedia.org/wiki/Universal_asynchronous_receiver-transmitter)
library for Python. It runs most places Python does and abstracts
lots of gnarly system details. However, some issues keep coming up:

- Most modern serial ports are USB, and get temporary names like
  `/dev/ttyACM3` or `COM4`. PySerial's
  [`serial.tools.list_ports.grep(...)`](https://pythonhosted.org/pyserial/tools.html#serial.tools.list_ports.grep)
  or Linux's
  [udev rules](https://dev.to/enbis/how-udev-rules-can-help-us-to-recognize-a-usb-to-serial-device-over-dev-tty-interface-pbk)
  require extra clumsy steps to use.

- Nonblocking or concurrent PySerial I/O is tricky and often
  [broken](https://github.com/pyserial/pyserial/issues/281)
  [entirely](https://github.com/pyserial/pyserial/issues/280).

- PySerial has small buffers; overruns lose data and/or block unexpectedly.

- PySerial doesn't lock ports by default; even when enabled, PySerial only
  uses one advisory locking method. Bad things happen when multiple programs
  try to use the same port.

The `ok-serial` library uses PySerial internally but has a revised interface:

- Ports are referenced by
  [port match expressions](#serial-port-match-expressions) with wildcard
  support, eg. `*RP2040*` or `2e43:0226` or `manufacturer="Arduino"`.

- I/O operations are thread safe and can be blocking, non-blocking,
  timeout-based, or async. Blocking operations can be interrupted.
  The semantics of concurrent access, partial reads/writes, interruption,
  I/O errors, and other edge cases are well defined.

- I/O buffers are limited only by system memory; writes never block.
  (A blocking drain is available.)

- Several [port locking modes](#sharing-modes) are supported, with exclusive
  locking by default. _All_ of
  [`/var/lock/LCK..*` files](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/ch05s09.html),
  [`flock(...)`](https://linux.die.net/man/2/flock) (like PySerial),
  and [`TIOCEXCL`](https://man7.org/linux/man-pages/man2/TIOCEXCL.2const.html)
  (as available) are used avoid contention.

- [`SerialPortTracker`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialPortTracker)
  is an automatic reconnection helper for graceful handling of pluggable
  devices.

## Installation

```bash
pip install ok-serial
```

(or `uv add ok-serial`, etc.)

## Usage

Here is a minimal example:

```
import ok_serial

conn = ok_serial.SerialConnection(match="MyDevice", baud=115200)
conn.write("Hello Device!")
while (data := conn.read_sync(timeout=5)):
    print("Received data:", data)
print("...5 seconds elapsed with no data")
```

(Note that `"MyDevice"` is a
[port match expression](#serial-port-match-expressions).)

API elements worth knowing include:

- [`SerialConnection`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection)
  \- establish a connection to a specific port and perform I/O
- [`scan_serial_ports`](https://egnor.github.io/ok-py-serial/ok_serial.html#scan_serial_ports)
  \- get all ports on the system, with descriptive attributes
- [`SerialPortMatcher`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialPortMatcher)
  \- use [port match expression strings](#serial-port-match-expressions) to
     identify ports of interest
- [`SerialPortTracker`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialPortTracker)
  \- scan and connect to a port with automatic error retry

Methods come in different flavors of blocking behavior:

- `*_sync` methods (eg.
  [`read_sync`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection.read_sync))
  block, accept `timeout=...`, and can raise
  [exceptions](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialIoException)
- `*_async` methods (eg.
  [`read_async`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection.read_async))
  return a
  [`Future`](https://docs.python.org/3/library/asyncio-future.html)
  the caller can
  [`await`](https://docs.python.org/3/reference/expressions.html#await)
  (see [asyncio](https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html))
  - Use
    [`asyncio.timeout`](https://docs.python.org/3/library/asyncio-task.html#timeouts)
    to add a timeout
  - Errors are reported via the `Future` (`await` will raise)
- Other methods (neither `*_sync` nor `*_async`) are non-blocking.

Methods and functions of any flavor are thread-safe and thread-sane, and
any error or closure on a connection interrupts all pending operations
on that connection.

See the [full API reference docs](https://egnor.github.io/ok-py-serial/)
for interface details.

## Serial port attributes

Serial ports can have attributes such as description text, USB vendor/product
ID, serial number and the like. These are captured as key/value pairs in
[`SerialPort.attr`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialPort.attr)
as returned by
[`scan_serial_ports`](https://egnor.github.io/ok-py-serial/ok_serial.html#scan_serial_ports).

Specific attribute keys
[come from PySerial](https://pyserial.readthedocs.io/en/latest/tools.html#serial.tools.list_ports.ListPortInfo)
and are platform/device dependent but usually include:

- `device` - system device name, eg. `/dev/ttyUSB1` or `COM3`
- `description` - human readable text, eg. `Arduino Uno`
- `manufacturer` - USB device manufacturer name, eg. `
- `vid_pid` - USB vendor and product ID, eg. `0403:6001`
- `serial_number` - USB device serial, eg. `DF62585783553434`
- `location` - system bus attachment path, eg. `3-2.1:1.0`

To see all the attributes, install `ok-serial`, connect some device(s) and
run `okserial --verbose`:

```text
Serial port: /dev/ttyACM3
   device='/dev/ttyACM3'
   name='ttyACM3'
   description='Feather RP2040 RFM - Pico Serial'
   hwid='USB VID:PID=239A:812D SER=DF62585783553434 LOCATION=3-2.1:1.0'
   vid='9114'
   pid='33069'
   serial_number='DF62585783553434'
   location='3-2.1:1.0'
   manufacturer='Adafruit'
   product='Feather RP2040 RFM'
   interface='Pico Serial'
   usb_device_path='/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1'
   device_path='/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1/3-2.1:1.0'
   subsystem='usb'
   usb_interface_path='/sys/devices/pci0000:00/0000:00:14.0/usb3/3-2/3-2.1/3-2.1:1.0'
   vid_pid='239A:812D'
...
```

## Serial port match expressions

Instead of plain device names, `ok-serial` can use **port match expressions**
to find ports. Match expressions are made of space-separated search terms:

- `word` - case INsensitive whole-word match in any attribute value
- `wild*word?` - `*` and `?` are wildcards (any text, single character)
- `1234`, `0xabcd` - hex or decimal values match hex or decimal equivalents
- `spaces\ and\ st\*rs\?` - special characters can be escaped with backslash...
- `"spaces and st*rs?"` - ...or with C/JS/Python-style quoted strings
- `attr="specific value"` - scoped to attribute prefix, must match whole value
- `~/regexp/` - case SENSITIVE regex match
  ([Python `re`](https://docs.python.org/3/library/re.html))
- `attr~/regexp/` - attribute-scoped partial regex match
- `attr~/^regexp$/` - attribute-scoped whole-value regex match

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
   ...
```

## Sharing modes

When opening a port,
[`SerialConnection`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection.__init__)
offers a choice of
[four sharing modes](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnectionOptions.sharing):

- `oblivious` (not recommended) - no locking is done and locks are ignored.
  Multiple users may end up sending and receiving data on the same port.
- `polite` - open fails if the port is locked. Once opened, no locks
  are held except for a shared lock to ward off other `polite` users. If a
  less polite program opens the port later there will be conflict.
  (In the future, this mode will attempt to notice such conflicts
  and close out the port, deferring to the less-polite program.)
- `exclusive` (the default) - open fails if the port is locked
  by a non-`polite` user. Once opened, locking protocols are used to prevent
  or discourage others from opening the port.
- `stomp` (use with care!) - _any other program using the port is killed_,
  if possible; regardless, locks are acquired, if possible; regardless,
  the port is opened, if possible.

Sharing mode implementation is limited by OS capabilities, process permissions,
and historical conventions of port usage coordination.
Best efforts are taken but your mileage may vary.
