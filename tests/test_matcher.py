"""Unit tests for ok_serial._scanning."""

import ok_serial
from ok_serial import SerialPort


WB, WE = r"(?<![A-Z0-9])", r"(?![A-Z0-9])"

PARSE_CHECKS = [
    (r"simple", [("", WB + r"simple" + WE)]),
    (
        " \twith whitespace\n ",
        [("", WB + r"with" + WE), ("", WB + r"whitespace" + WE)],
    ),
    (r"wild*card?expr", [("", WB + r"wild.*card.expr" + WE)]),
    (r"wild\*card\?expr", [("", WB + r"wild\*card\?expr" + WE)]),
    (r"field='don\'t panic'", [("field", r"^don't\ panic$")]),
    (r'a=" quoted: \"string\" "', [("a", r'^\ quoted:\ "string"\ $')]),
    (r'a=avalue b="bvalue"', [("a", r"^avalue$"), ("b", r"^bvalue$")]),
    (
        r"""val a='av' etc b="bv" etc""",
        [
            ("", WB + r"val" + WE),
            ("a", r"^av$"),
            ("", WB + r"etc" + WE),
            ("b", r"^bv$"),
            ("", WB + r"etc" + WE),
        ],
    ),
    (r"0", [("", WB + r"(0|0*0|(0x)?0*0h?)" + WE)]),
    (r"a=123", [("a", r"^(123|0*123|(0x)?0*7bh?)$")]),
    (r"0b1010", [("", WB + r"(0b1010|0*10|(0x)?0*ah?)" + WE)]),
    (r"0x07B", [("", WB + r"(0x07B|0*123|(0x)?0*7bh?)" + WE)]),
    (r"01ab:23cd", [("vid", r"^427$"), ("pid", r"^9165$")]),
]


def test_SerialPortMatcher_init():
    for spec, expected in PARSE_CHECKS:
        actual = ok_serial.SerialPortMatcher(spec)._patterns
        actual_unwrapped = [(k, rx.pattern) for k, rx in actual]
        assert actual_unwrapped == expected


def test_SerialPortMatcher_matches():
    matcher = ok_serial.SerialPortMatcher("*mid* a* *b")
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
