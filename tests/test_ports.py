"""Unit tests for ok_serial._ports."""

from ok_serial._ports import PortMatcher, PortIdentity


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


def test_PortMatcher_matches():
    matcher = PortMatcher("*mid* A:a* b:*b")
    assert matcher.matches(
        PortIdentity(id="z", attr={"a": "axx", "b": "xxb", "c": "xmidx"})
    )
    assert matcher.matches(
        PortIdentity(id="z", attr={"a": "Axx", "b": "xxB", "c": "xMIDx"})
    )
    assert matcher.matches(PortIdentity(id="z", attr={"a": "Amid", "b": "xxB"}))

    assert not matcher.matches(
        PortIdentity(id="z", attr={"a": "xxa", "b": "xxb", "c": "xmidx"})
    )
    assert not matcher.matches(
        PortIdentity(id="z", attr={"a": "axx", "b": "bxx", "c": "xmidx"})
    )
    assert not matcher.matches(
        PortIdentity(id="z", attr={"a": "axx", "b": "xxb", "c": "xmadx"})
    )
