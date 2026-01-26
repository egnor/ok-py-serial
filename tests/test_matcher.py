"""Unit tests for ok_serial._scanning."""

import ok_serial
from ok_serial import SerialPort


WB, WE = "(?<![A-Z0-9])", "(?![A-Z0-9])"

PARSE_CHECKS = [
    ("simple", [f"~/{WB}simple{WE}/"]),
    ("!simple", [f"!~/{WB}simple{WE}/"]),
    (
        " \twith whitespace\n ",
        [f"~/{WB}with{WE}/", f"~/{WB}whitespace{WE}/"],
    ),
    ("wild*card?expr", [f"~/{WB}wild.*card.expr{WE}/"]),
    ("wild\\*card\\?expr", [f"~/{WB}wild\\*card\\?expr{WE}/"]),
    ("field='don\\'t panic'", ["field~/^don't\\ panic$/"]),
    ("field!='do panic'", ["field!~/^do\\ panic$/"]),
    (r'a=" quoted: \"string\" "', [r'a~/^\ quoted:\ "string"\ $/']),
    (r'a=avalue b!="bvalue"', ["b!~/^bvalue$/", "a~/^avalue$/"]),
    (
        """val a='av' etc b!="bv" !etc""",
        [
            "b!~/^bv$/",
            f"!~/{WB}etc{WE}/",
            f"~/{WB}val{WE}/",
            "a~/^av$/",
            f"~/{WB}etc{WE}/",
        ],
    ),
    ("0", [f"~/{WB}(0|0*0|(0x)?0*0h?){WE}/"]),
    ("a=123", ["a~/^(123|0*123|(0x)?0*7bh?)$/"]),
    ("0b1010", [f"~/{WB}(0b1010|0*10|(0x)?0*ah?){WE}/"]),
    ("!0x07B", [f"!~/{WB}(0x07B|0*123|(0x)?0*7bh?){WE}/"]),
    ("01ab:23cd", [f"~/{WB}01ab:23cd{WE}/"]),
]


def test_SerialPortMatcher_init():
    for spec, expected in PARSE_CHECKS:
        actual = ok_serial.SerialPortMatcher(spec)
        assert actual.patterns() == expected


def test_SerialPortMatcher_filter():
    input = [
        SerialPort(name="z1", attr={"a": "axx", "b": "xxb", "c": "xmidx"}),
        SerialPort(name="z2", attr={"a": "Axx", "b": "xxB", "c": "xMIDx"}),
        SerialPort(name="z3", attr={"a": "Amid", "b": "xxB"}),
        SerialPort(name="z4", attr={"a": "xxa", "b": "xxb", "c": "xmidx"}),
        SerialPort(name="z5", attr={"a": "axx", "b": "bxx", "c": "xmidx"}),
        SerialPort(name="z6", attr={"a": "axx", "b": "xxb", "c": "xmadx"}),
    ]

    matcher = ok_serial.SerialPortMatcher("")
    output = matcher.filter(input)
    assert output == input

    matcher = ok_serial.SerialPortMatcher("*mid* a* !*b")
    output = matcher.filter(input)
    assert output == [
        SerialPort(name="z5", attr={"a": "axx", "b": "bxx", "c": "xmidx"}),
    ]
