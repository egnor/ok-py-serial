"""Unit tests for ok_serial._scanning."""

import serial.tools.list_ports
import serial.tools.list_ports_common

from ok_serial import _scanning


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


def test_PortMatcher_init():
    for spec, expected in PARSE_CHECKS:
        actual = _scanning.PortMatcher(spec)._patterns
        actual_unwrapped = {k: rx.pattern for k, rx in actual.items()}
        assert actual_unwrapped == expected


def test_PortMatcher_matches():
    PortAttr = _scanning.PortAttributes
    matcher = _scanning.PortMatcher("*mid* A:a* b:*b")
    for id in [
        PortAttr(port="z", attr={"a": "axx", "b": "xxb", "c": "xmidx"}),
        PortAttr(port="z", attr={"a": "Axx", "b": "xxB", "c": "xMIDx"}),
        PortAttr(port="z", attr={"a": "Amid", "b": "xxB"}),
    ]:
        assert matcher.matches(id)

    for id in [
        PortAttr(port="z", attr={"a": "xxa", "b": "xxb", "c": "xmidx"}),
        PortAttr(port="z", attr={"a": "axx", "b": "bxx", "c": "xmidx"}),
        PortAttr(port="z", attr={"a": "axx", "b": "xxb", "c": "xmadx"}),
    ]:
        assert not matcher.matches(id)


def test_scan_ports(mocker):
    mocker.patch("serial.tools.list_ports.comports")

    bare_port = serial.tools.list_ports_common.ListPortInfo("/dev/zz")

    full_port = serial.tools.list_ports_common.ListPortInfo("/dev/full")
    full_port.description = "Description"
    full_port.hwid = "HwId"
    full_port.vid = 111
    full_port.pid = 222
    full_port.serial_number = "Serial"
    full_port.location = "Location"
    full_port.manufacturer = "Manufacturer"
    full_port.product = "Product"
    full_port.interface = "Interface"

    serial.tools.list_ports.comports.return_value = [bare_port, full_port]

    PortAttr = _scanning.PortAttributes
    assert _scanning.scan_ports() == [
        PortAttr(
            port="/dev/full",
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
        PortAttr(port="/dev/zz", attr={"device": "/dev/zz", "name": "zz"}),
    ]
