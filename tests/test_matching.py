import ok_serial
from ok_serial._matching import compile_match


def _filter(spec, ports):
    pred = compile_match(spec)
    return [p for p in ports if pred(p)]


def test_match_none_and_empty():
    ports = [
        ok_serial.SerialPort(name="a", attr={"x": "1"}),
        ok_serial.SerialPort(name="b", attr={}),
    ]
    assert _filter(None, ports) == ports
    assert _filter("", ports) == ports
    assert _filter("   \t\n", ports) == ports


def test_match_callable_passthrough():
    ports = [
        ok_serial.SerialPort(name="a", attr={"x": "1"}),
        ok_serial.SerialPort(name="b", attr={"x": "2"}),
    ]
    pred = compile_match(lambda p: p.attr["x"] == "2")
    assert [p for p in ports if pred(p)] == [ports[1]]


def test_whole_word_match():
    ports = [
        ok_serial.SerialPort(name="a", attr={"device": "/dev/ttyS1"}),
        ok_serial.SerialPort(name="b", attr={"device": "/dev/ttyS10"}),
    ]
    # bare token: whole-word, no /dev/ttyS10 false hit
    assert _filter("ttyS1", ports) == [ports[0]]
    # explicit prefix glob:
    assert _filter("ttyS1*", ports) == ports
    # case-insensitive:
    assert _filter("TTYS1", ports) == [ports[0]]


def test_multi_attribute():
    ports = [
        ok_serial.SerialPort(
            name="z1",
            attr={"description": "Pico Serial", "vid_pid": "2e8a:0005"},
        ),
        ok_serial.SerialPort(
            name="z2",
            attr={"description": "Arduino Uno", "vid_pid": "2341:0043"},
        ),
    ]
    # tokens AND'd, each must hit some attribute
    assert _filter("Pico 2e8a:0005", ports) == [ports[0]]
    assert _filter("Pico 2341:0043", ports) == []  # no port has both
    assert _filter("pico serial", ports) == [ports[0]]


def test_wildcards():
    ports = [
        ok_serial.SerialPort(name="a", attr={"serial_number": "DF62585783"}),
        ok_serial.SerialPort(name="b", attr={"serial_number": "AB12345678"}),
    ]
    assert _filter("DF625*", ports) == [ports[0]]
    assert _filter("*5783", ports) == [ports[0]]
    assert _filter("DF62?85783", ports) == [ports[0]]
    assert _filter("DF625", ports) == []  # not whole word


def test_word_boundaries_around_punctuation():
    # `:` and `/` count as word boundaries, so partial vid:pid works
    ports = [
        ok_serial.SerialPort(name="a", attr={"vid_pid": "2e8a:0005"}),
    ]
    assert _filter("2e8a", ports) == ports
    assert _filter("0005", ports) == ports
