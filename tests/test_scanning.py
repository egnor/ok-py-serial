"""Unit tests for ok_serial._scanning."""

import json
import pytest
from serial.tools import list_ports
from serial.tools import list_ports_common

import ok_serial
from ok_serial import SerialPort


PARSE_CHECKS = [
    ("simple", {"*": r"(?s:simple)\Z"}),
    # fnmatch will backslash whitespace (space, \t, \n, etc) for some reason
    (" \twith whitespace\n ", {"*": "(?s:\\ \\\twith\\ whitespace\\\n\\ )\\Z"}),
    ("wild*card?expr", {"*": r"(?s:wild.*card.expr)\Z"}),
    ("field:wild*card?expr", {"field": r"(?s:wild.*card.expr)\Z"}),
    ("  field  :  wild*card?expr", {"field": r"(?s:wild.*card.expr)\Z"}),
    ("a:avalue b:bvalue", {"a": r"(?s:avalue)\Z", "b": r"(?s:bvalue)\Z"}),
    (
        "val a:av etc b:bv etc",
        {"*": r"(?s:val)\Z", "a": r"(?s:av\ etc)\Z", "b": r"(?s:bv\ etc)\Z"},
    ),
    ('a: " quoted: \\"string\\" "', {"a": r'(?s:\ quoted:\ "string"\ )\Z'}),
    ("0", {"*": r"(0|0|(0x)?0*0h?)\Z"}),
    ("123", {"*": r"(123|123|(0x)?0*7bh?)\Z"}),
    ("0x07B", {"*": r"(0x07B|123|(0x)?0*7bh?)\Z"}),
]


def test_SerialPortMatcher_init():
    for spec, expected in PARSE_CHECKS:
        actual = ok_serial.SerialPortMatcher(spec)._patterns
        actual_unwrapped = {k: rx.pattern for k, rx in actual.items()}
        assert actual_unwrapped == expected


def test_SerialPortMatcher_matches():
    matcher = ok_serial.SerialPortMatcher("*mid* A:a* b:*b")
    for id in [
        SerialPort(name="z", attr={"a": "axx", "b": "xxb", "c": "xmidx"}),
        SerialPort(name="z", attr={"a": "Axx", "b": "xxB", "c": "xMIDx"}),
        SerialPort(name="z", attr={"a": "Amid", "b": "xxB"}),
    ]:
        assert matcher.matches(id)

    for id in [
        SerialPort(name="z", attr={"a": "xxa", "b": "xxb", "c": "xmidx"}),
        SerialPort(name="z", attr={"a": "axx", "b": "bxx", "c": "xmidx"}),
        SerialPort(name="z", attr={"a": "axx", "b": "xxb", "c": "xmadx"}),
    ]:
        assert not matcher.matches(id)


def test_scan_ports(mocker):
    mocker.patch("serial.tools.list_ports.comports")

    bare_port = list_ports_common.ListPortInfo("/dev/zz")

    full_port = list_ports_common.ListPortInfo("/dev/full")
    full_port.description = "Description"
    full_port.hwid = "HwId"
    full_port.vid = 111
    full_port.pid = 222
    full_port.serial_number = "Serial"
    full_port.location = "Location"
    full_port.manufacturer = "Manufacturer"
    full_port.product = "Product"
    full_port.interface = "Interface"

    list_ports.comports.return_value = [bare_port, full_port]

    assert ok_serial.scan_serial_ports() == [
        SerialPort(
            name="/dev/full",
            attr={
                "device": "/dev/full",
                "name": "full",
                "description": "Description",
                "hwid": "HwId",
                "vid": "111",
                "pid": "222",
                "serial_number": "Serial",
                "manufacturer": "Manufacturer",
                "product": "Product",
                "interface": "Interface",
                "location": "Location",
            },
        ),
        SerialPort(name="/dev/zz", attr={"device": "/dev/zz", "name": "zz"}),
    ]


def test_scan_ports_with_override(monkeypatch, tmp_path):
    override_path = tmp_path / "scan_override.json"
    monkeypatch.setenv("OK_SERIAL_SCAN_OVERRIDE", str(override_path))
    with pytest.raises(ok_serial.SerialScanException):
        ok_serial.scan_serial_ports()  # fails: file does not exist

    override_path.write_text("bad json")
    with pytest.raises(ok_serial.SerialScanException):
        ok_serial.scan_serial_ports()  # fails: format is invalid

    override_path.write_text(json.dumps({"bad": {"entry": None}}))
    with pytest.raises(ok_serial.SerialScanException):
        ok_serial.scan_serial_ports()  # fails: structure is invalid

    override = {"port1": {"aname": "avalue", "bname": "bvalue"}, "port2": {}}
    override_path.write_text(json.dumps(override))

    assert ok_serial.scan_serial_ports() == [
        SerialPort(name="port1", attr={"aname": "avalue", "bname": "bvalue"}),
        SerialPort(name="port2", attr={}),
    ]
