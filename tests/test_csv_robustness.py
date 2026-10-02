"""MAJOR-1 regression tests: atomic CSV write + tolerant parsing.

Before the fix, _write_energy_file truncated energy_data.csv in place (a
crash mid-write destroyed the whole local history) and _read_energy_file
raised on the first corrupted line, killing every future import until the
user deleted the file by hand.
"""
import datetime
import logging
import os

import pytest

from custom_components.enchufado.coordinator import EnchufadoCoordinator
from custom_components.enchufado.util import madrid_timestamp


class Boom:
    """Value whose str() raises — simulates a crash mid-write."""

    def __str__(self):
        raise RuntimeError("simulated crash mid-write")


def _hour(ts_dt: datetime.datetime, value: float = 0.5):
    ts = madrid_timestamp(ts_dt)
    return ts, {"value": value, "reading_type": "R"}


def _write(tmp_path, consumptions, prices):
    path = str(tmp_path / "energy_data.csv")
    EnchufadoCoordinator._write_energy_file(path, consumptions, prices)
    return path


def test_write_is_atomic_no_tmp_left_behind(tmp_path):
    consumptions = dict([_hour(datetime.datetime(2026, 3, 1, 0))])
    path = _write(tmp_path, consumptions, {})

    assert "date,timestamp,consumption,price,reading_type" in open(path).read()
    assert not os.path.exists(path + ".tmp")


def test_crash_mid_write_leaves_previous_file_intact(tmp_path):
    """Simulate a crash mid-write: the temp file is the one being written, so
    the pre-existing good file must survive byte-for-byte."""
    good_hours = [_hour(datetime.datetime(2026, 3, 1, h)) for h in range(24)]
    path = _write(tmp_path, dict(good_hours), {})
    good_bytes = open(path, "rb").read()

    ts, entry = _hour(datetime.datetime(2026, 3, 2, 0))
    consumptions = dict(good_hours)
    consumptions[ts] = entry
    with pytest.raises(RuntimeError):
        EnchufadoCoordinator._write_energy_file(path, consumptions, {ts: Boom()})

    # The old history is untouched; the torn data only exists in the tmp file.
    assert open(path, "rb").read() == good_bytes
    assert os.path.exists(path + ".tmp")


def test_successful_rewrite_recovers_after_a_crashed_write(tmp_path):
    path = str(tmp_path / "energy_data.csv")
    with pytest.raises(RuntimeError):
        EnchufadoCoordinator._write_energy_file(path, {}, {0: Boom()})

    consumptions = dict([_hour(datetime.datetime(2026, 3, 1, 0))])
    EnchufadoCoordinator._write_energy_file(path, consumptions, {})

    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")
    consumptions_out, prices_out = EnchufadoCoordinator._read_energy_file(path)
    ts = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
    assert consumptions_out == {ts: {"value": 0.5, "reading_type": "R"}}
    assert prices_out == {}


def test_corrupted_line_is_skipped_not_fatal(tmp_path, caplog):
    path = tmp_path / "energy_data.csv"
    ts0 = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
    ts1 = madrid_timestamp(datetime.datetime(2026, 3, 1, 1))
    ts2 = madrid_timestamp(datetime.datetime(2026, 3, 1, 2))
    path.write_text(
        "date,timestamp,consumption,price,reading_type\n"
        f"01/03/2026 0,{ts0},0.5,0.1,R\n"
        "GARBAGE LINE,,$$\n"  # corrupted
        f"01/03/2026 2,{ts2},0.9,0.3,R\n"
        f"{ts1}\n"  # truncated final line
    )

    with caplog.at_level(logging.WARNING):
        consumptions, prices = EnchufadoCoordinator._read_energy_file(str(path))

    assert consumptions == {
        ts0: {"value": 0.5, "reading_type": "R"},
        ts2: {"value": 0.9, "reading_type": "R"},
    }
    assert prices == {ts0: 0.1, ts2: 0.3}
    assert any("corrupted line" in r.message for r in caplog.records)


def test_blank_line_is_skipped(tmp_path):
    path = tmp_path / "energy_data.csv"
    ts0 = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
    path.write_text(
        "date,timestamp,consumption,price,reading_type\n"
        f"01/03/2026 0,{ts0},0.5,0.1,R\n"
        "\n"
    )
    consumptions, prices = EnchufadoCoordinator._read_energy_file(str(path))
    assert consumptions == {ts0: {"value": 0.5, "reading_type": "R"}}
    assert prices == {ts0: 0.1}


def test_corrupted_price_only_drops_price(tmp_path):
    path = tmp_path / "energy_data.csv"
    ts0 = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
    ts1 = madrid_timestamp(datetime.datetime(2026, 3, 1, 1))
    path.write_text(
        "date,timestamp,consumption,price,reading_type\n"
        f"01/03/2026 0,{ts0},0.5,0.1,R\n"
        f"01/03/2026 1,{ts1},0.5,not-a-float,R\n"
    )
    consumptions, prices = EnchufadoCoordinator._read_energy_file(str(path))
    # Both rows are structurally fine; the bad float only invalidates the
    # field it belongs to.
    assert ts1 in consumptions
    assert ts1 not in prices
