import re

from ok_serial._port import PortPredicate


def compile_match(spec: str | PortPredicate | None) -> PortPredicate:
    """Returns a predicate selecting matching `PortInfo` objects.

    A `None` or empty string accepts any port. A string is split on whitespace
    into glob tokens; each token must match (case-insensitively, as a
    whole-word glob with `*` / `?` wildcards) somewhere in the device name or
    some attribute value. A callable is returned as-is.

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

    # the device name is matchable too, so a port with no attributes at all
    # (eg. from $OK_SERIAL_SCAN_OVERRIDE) can still be named or globbed
    return lambda port: all(
        any(t.search(v) for v in (port.name, *port.attr.values()))
        for t in tokens
    )


def _compile_token(token: str) -> re.Pattern:
    body = "".join(
        ".*" if ch == "*" else "." if ch == "?" else re.escape(ch)
        for ch in token
    )

    # Custom word boundary: don't start or end in a run of alphanumerics,
    # to avoid "ttyS1" matching "ttyS10".
    boundary = r"(?!(?<=[A-Z0-9])[A-Z0-9])"
    return re.compile(boundary + body + boundary, re.I)
