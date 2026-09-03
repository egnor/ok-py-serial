import json
import pytest

import ok_serial

import psutil._ntuples


def test_scan_uf2_devices(mocker, tmpdir):
    xx_dir = tmpdir.join("xx")  # does not exist
    xx_part = psutil._ntuples.sdiskpart(
        device="/dev/xx", mountpoint=str(xx_dir), fstype="vfat", opts=""
    )

    yy_dir = tmpdir.mkdir("yy")
    yy_dir.join("INFO_UF2.TXT").write("")  # no content
    yy_part = psutil._ntuples.sdiskpart(
        device="/dev/yy", mountpoint=str(yy_dir), fstype="vfat", opts=""
    )

    zz_dir = tmpdir.mkdir("zz")
    zz_dir.join("INFO_UF2.TXT").write(
        "Fake Bootloader\r\n"
        "Board-ID: Fake-Board\r\n"
        "Invalid Line\r\n"
        "Model: Fake Model\r\n"
    )
    zz_part = psutil._ntuples.sdiskpart(
        device="/dev/zz", mountpoint=str(zz_dir), fstype="vfat", opts=""
    )

    mock_partitions = mocker.patch("psutil.disk_partitions")
    mock_partitions.return_value = [xx_part, yy_part, zz_part]
    assert ok_serial.scan_uf2_devices() == [
        ok_serial.PortInfo(
            name=str(zz_dir),
            attr={
                "device": str(zz_dir),
                "uf2": "Fake Bootloader",
                "board-id": "Fake-Board",
                "model": "Fake Model",
            },
        ),
    ]


def test_scan_ports_with_override(monkeypatch, tmp_path):
    override_path = tmp_path / "scan_uf2_override.json"
    monkeypatch.setenv("OK_SERIAL_SCAN_UF2_OVERRIDE", str(override_path))
    with pytest.raises(ok_serial.SerialScanException):
        ok_serial.scan_uf2_devices()  # fails: file does not exist

    override_path.write_text("bad json")
    with pytest.raises(ok_serial.SerialScanException):
        ok_serial.scan_uf2_devices()  # fails: format is invalid

    override_path.write_text(json.dumps({"bad": {"entry": None}}))
    with pytest.raises(ok_serial.SerialScanException):
        ok_serial.scan_uf2_devices()  # fails: structure is invalid

    override = {"port1": {"aname": "avalue", "bname": "bvalue"}, "port2": {}}
    override_path.write_text(json.dumps(override))

    assert ok_serial.scan_uf2_devices() == [
        ok_serial.PortInfo(
            name="port1", attr={"aname": "avalue", "bname": "bvalue"}
        ),
        ok_serial.PortInfo(name="port2", attr={}),
    ]
