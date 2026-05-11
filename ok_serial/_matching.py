import logging
import re
from collections.abc import Callable

from ok_serial._scanning import SerialPort

log = logging.getLogger("ok_serial.matching")

PortPredicate = Callable[[SerialPort], bool]
"""A function that returns True for ports of interest."""


def compile_match(spec: str | PortPredicate | None) -> PortPredicate:
    """Returns a predicate selecting matching `SerialPort` objects.

    A `None` or empty string accepts any port. A string is split on whitespace
    into glob tokens; each token must match (case-insensitively, as a
    whole-word glob with `*` / `?` wildcards) somewhere in some attribute
    value. A callable is returned as-is.

    For anything fancier than that (substring matching across attribute
    boundaries, regex, negation, etc.) pass a callable instead.
    """

    if spec is None or spec == "":
        return lambda p: True
    if callable(spec):
        return spec
    tokens = [_compile_token(t) for t in spec.split()]
    if not tokens:
        return lambda p: True

    return lambda port: all(
        any(t.search(v) for v in port.attr.values()) for t in tokens
    )


def _compile_token(token: str) -> re.Pattern:
    body = "".join(
        ".*" if ch == "*" else "." if ch == "?" else re.escape(ch)
        for ch in token
    )
    # Custom word boundary: treat any non-alphanumeric character (including _)
    # as a separator, so "ttyS1" doesn't match "ttyS10" but spans / and : etc.
    return re.compile(r"(?<![A-Z0-9])" + body + r"(?![A-Z0-9])", re.I)
