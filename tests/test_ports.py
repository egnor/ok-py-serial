"""Unit tests for ok_serial._ports."""

from ok_serial._ports import PortMatcher


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
]


def test_PortMatcher_init():
    for spec, expected in PARSE_CHECKS:
        actual = PortMatcher(spec)._patterns
        actual_unwrapped = {k: rx.pattern for k, rx in actual.items()}
        assert actual_unwrapped == expected
