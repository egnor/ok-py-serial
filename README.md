# OK serial I/O for Python &nbsp; 🔌〡〇〡〇〡🐍

A Python serial port library (based on [PySerial](https://www.pyserial.com/)) with improved port discovery and I/O semantics. [(API reference)](https://egnor.github.io/ok-py-serial/)

Think twice before using this library! Consider something more established:

- [good old PySerial](https://www.pyserial.com/) - the implementation under `ok-serial`, well established and widely used
- [pyserial-asyncio](https://github.com/pyserial/pyserial-asyncio) - official and "proper" [asyncio](https://docs.python.org/3/library/asyncio.html) support for PySerial
- [pyserial-asyncio-fast](https://github.com/home-assistant-libs/pyserial-asyncio-fast) - pyserial-asyncio fork designed for faster writes
- [aioserial](https://github.com/mrjohannchang/aioserial.py) - alternative asyncio wrapper designed for ease of use
- bonus recommendation: [tio](https://github.com/tio/tio) - not a library, not Python, but a great serial terminal utility

## Purpose

Since 2001, [PySerial](https://www.pyserial.com/) has been the workhorse [serial port](https://en.wikipedia.org/wiki/Serial_port) / [UART](https://en.wikipedia.org/wiki/Universal_asynchronous_receiver-transmitter) library for Python. It runs most places Python does and abstracts lots of gnarly system details. However, some issues keep coming up:

- Most modern serial ports are USB, and get temporary names like `/dev/ttyACM3` or `COM4`. PySerial's [`serial.tools.list_ports.grep(...)`](https://pythonhosted.org/pyserial/tools.html#serial.tools.list_ports.grep) or Linux's [udev rules](https://dev.to/enbis/how-udev-rules-can-help-us-to-recognize-a-usb-to-serial-device-over-dev-tty-interface-pbk) can help but require extra steps to use.

- Nonblocking or concurrent PySerial I/O is tricky and often [broken](https://github.com/pyserial/pyserial/issues/281) [entirely](https://github.com/pyserial/pyserial/issues/280).

- PySerial has small buffers; overruns lose data and/or block unexpectedly.

- PySerial doesn't lock ports by default, and only supports one advisory locking method. Bad things happen when multiple programs try to use the same port.

The `ok-serial` library uses PySerial internally but has a revised interface:

- Ports are referenced by [match strings](#port-matching) with wildcard support (eg. `RP2040` or `2e43:0226`) or, for by arbitrary `SerialPort -> bool` callables.

- I/O operations are thread safe and can be blocking, non-blocking, timeout-based, or async. Blocking operations can be cleanly interrupted. The semantics of concurrent access, partial reads/writes, interruption, I/O errors, and other edge cases are well defined.

- I/O buffers are limited only by system memory; writes never block. (A blocking drain is available.)

- Several [port locking modes](#sharing-modes) are supported, with exclusive locking by default. _All_ of [`/var/lock/LCK..*` files](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/ch05s09.html), [`flock(...)`](https://linux.die.net/man/2/flock) (like PySerial), and [`TIOCEXCL`](https://man7.org/linux/man-pages/man2/TIOCEXCL.2const.html) are used to avoid contention.

- [`SerialPortTracker`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialPortTracker) is an automatic reconnection helper for graceful handling of pluggable devices.

## Installation

```bash
pip install ok-serial
```

(or `uv add ok-serial`, etc.)

Install `ok-serial[cli]` to get dependencies for the `okserial` CLI tool.

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

(Note that `"MyDevice"` is a [port match expression](#port-matching).)

API elements worth knowing include:

- [`SerialConnection`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection) - establish a connection to a specific port and perform I/O
- [`scan_serial_ports`](https://egnor.github.io/ok-py-serial/ok_serial.html#scan_serial_ports) - get all ports on the system, with descriptive attributes
- [`SerialPortTracker`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialPortTracker) - scan and connect to a port with automatic error retry

I/O methods come in different flavors:

- `*_sync` methods (eg. [`read_sync`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection.read_sync)) block, accept `timeout=...`, and can raise [exceptions](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialIoException)
- `*_async` methods (eg. [`read_async`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection.read_async)) return an [`await`-able coroutine](https://docs.python.org/3/reference/expressions.html#await) for [asyncio](https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html)
  - Use [`asyncio.timeout`](https://docs.python.org/3/library/asyncio-task.html#timeouts) to add a timeout
  - Errors are reported via the coroutine (`await` will raise)
- Other methods (eg. [`write`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection.write)) are non-blocking.

All methods and functions are thread-safe and thread-sane. Any error or closure on a connection interrupts all operations on that connection.

See the [full API reference docs](https://egnor.github.io/ok-py-serial/) for interface details.

## Serial port attributes

Serial ports have metadata attributes like descriptive text, USB vendor/product ID, serial number and the like. These are captured as key/value pairs in [`SerialPort.attr`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialPort.attr) and returned by [`scan_serial_ports`](https://egnor.github.io/ok-py-serial/ok_serial.html#scan_serial_ports).

Specific attributes [come from PySerial](https://pyserial.readthedocs.io/en/latest/tools.html#serial.tools.list_ports.ListPortInfo) and are platform/device dependent but typically include:

- `device` - system device name, eg. `/dev/ttyUSB1` or `COM3`
- `description` - human readable text, eg. `Arduino Uno`
- `manufacturer` - USB device manufacturer name, eg. `
- `vid_pid` - USB vendor and product ID, eg. `0403:6001`
- `serial_number` - USB device serial, eg. `DF62585783553434`
- `location` - system bus attachment path, eg. `3-2.1:1.0`

To see all the attributes, install `ok-serial[cli]`, connect some device(s) and run `okserial list --print-verbose`:

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

## Port matching

`SerialConnection(match=...)` and `SerialPortTracker(...)` take either a **match string** or a **predicate callable** (`SerialPort -> bool`).

A match string is split on whitespace into glob tokens. Each token must match (case-insensitively, as a whole-word glob with `*` and `?` wildcards) somewhere in some attribute value. So:

- `Pico` - some attribute contains the word `pico` (any case)
- `RP2040 DF625*` - some attribute contains `rp2040`, and some attribute
  contains a word starting with `df625`
- `2e8a:0005` - matches the canonical `vid_pid` form (lowercase hex)
- `ttyS1` - does NOT match `/dev/ttyS10`; use `ttyS1*` for prefix matching

Word boundaries treat any non-alphanumeric character (`/`, `:`, `_`, etc.) as a separator, so partial USB IDs and device-path fragments work naturally.

For anything more elaborate (substring matching across attribute boundaries, regex, negation, etc.), pass a callable:

```python
ok_serial.SerialPortTracker(
    match=lambda p: p.attr.get("manufacturer") == "Adafruit"
    and p.attr.get("serial_number", "").startswith("DF625"),
)
```

Run `okserial list` to see which ports are visible and what attributes they have.

## Sharing modes

When opening a port, [`SerialConnection`](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnection.__init__) offers a choice of [sharing modes](https://egnor.github.io/ok-py-serial/ok_serial.html#SerialConnectionOptions.sharing):

- `oblivious` (not recommended) - Checks no locks and holds no locks. Multiple programs may open the port at once, leading to corruption.
- `polite` - Checks for locks before opening the port, but holds no locks while running. Abandons the port if another program is detected using it.
- `exclusive` (the default) - Checks for locks before opening the port, and holds locks to guard against other programs using the port.
- `stomp` (use with care!) - _Any other program using the port is killed_, if possible; locks are held, if possible; the port is opened regardless.

Sharing mode implementation is limited by OS capabilities, process permissions, and historical conventions of port usage coordination. Best efforts are taken but your mileage may vary.
