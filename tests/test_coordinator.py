"""Unit tests for coordinator helpers: CSV persistence, statistics, periods."""
import datetime

import pytest

from custom_components.enchufado.coordinator import EnchufadoCoordinator as C
from custom_components.enchufado.util import MADRID_TZ, madrid_timestamp


class ExecutorStub:
    """Minimal hass stand-in exposing async_add_executor_job."""

    def __init__(self):
        self.loop_called = 0

    async def async_add_executor_job(self, func, *args):
        self.loop_called += 1
        return func(*args)


def ts(d: datetime.date, hour: int) -> int:
    return madrid_timestamp(datetime.datetime(d.year, d.month, d.day, hour))


# ------------------------------------------------------------------ energy CSV

async def test_energy_file_roundtrip(tmp_path):
    path = str(tmp_path / "energy_data.csv")
    d = datetime.date(2026, 3, 10)
    consumptions = {ts(d, h): {"value": 0.25, "reading_type": "R"} for h in range(24)}
    prices = {ts(d, h): 0.12345 for h in range(24)}
    hass = ExecutorStub()
    await C.save_energy_data(hass, path, consumptions, prices)
    loaded_c, loaded_p = await C.load_energy_data(hass, path)
    assert loaded_c == consumptions
    assert loaded_p == prices


async def test_energy_file_legacy_format_without_reading_type(tmp_path):
    """Files written before reading_type existed must still parse."""
    path = tmp_path / "energy_data.csv"
    d = datetime.date(2026, 1, 5)
    t = ts(d, 3)
    path.write_text(f"date,timestamp,consumption,price\n05/01/2026 03,{t},0.5,0.1\n")
    hass = ExecutorStub()
    consumptions, prices = await C.load_energy_data(hass, str(path))
    assert consumptions[t] == {"value": 0.5, "reading_type": ""}
    assert prices[t] == 0.1


async def test_energy_file_skips_empty_and_dash_fields(tmp_path):
    path = tmp_path / "energy_data.csv"
    t1, t2 = 1767229200, 1767232800
    path.write_text(
        "date,timestamp,consumption,price,reading_type\n"
        f"x,{t1},,-,R\n"
        f"x,{t2},-,0.2,E\n"
    )
    hass = ExecutorStub()
    consumptions, prices = await C.load_energy_data(hass, str(path))
    assert t1 not in consumptions and t1 not in prices
    assert t2 not in consumptions
    assert prices[t2] == 0.2


# ------------------------------------------------------------------ statistics

def test_create_statistics_sums_and_states():
    d = datetime.date(2026, 5, 4)
    consumptions = {ts(d, h): {"value": 1.0, "reading_type": "R"} for h in range(24)}
    prices = {ts(d, h): 0.1 for h in range(24)}
    c_stats, cost_stats = C.create_statistics(0, consumptions, prices, 0, 0)
    assert len(c_stats) == 24
    # running sum (StatisticData is a TypedDict → plain dicts)
    assert c_stats[-1]["sum"] == pytest.approx(24.0)
    assert cost_stats[-1]["sum"] == pytest.approx(2.4)
    # day state resets at midnight Madrid
    midnight_idx = next(
        i for i, s in enumerate(c_stats)
        if s["start"].astimezone(MADRID_TZ).hour == 0
    )
    assert c_stats[midnight_idx]["state"] == pytest.approx(1.0)


def test_create_statistics_negative_prices_supported():
    d = datetime.date(2026, 5, 4)
    consumptions = {ts(d, h): {"value": 2.0, "reading_type": "R"} for h in range(24)}
    prices = {ts(d, h): (-0.05 if h < 6 else 0.1) for h in range(24)}
    _, cost_stats = C.create_statistics(0, consumptions, prices, 0, 0)
    assert cost_stats[0]["sum"] < 0  # negative-price hours reduce the running cost


def test_create_statistics_from_offset_and_totals():
    d = datetime.date(2026, 5, 4)
    consumptions = {ts(d, h): {"value": 1.0, "reading_type": "R"} for h in range(24)}
    prices = {ts(d, h): 0.2 for h in range(24)}
    start_ts = ts(d, 12)
    c_stats, cost_stats = C.create_statistics(start_ts, consumptions, prices, 100.0, 10.0)
    assert len(c_stats) == 11  # resumes at the hour AFTER the last recorded one
    assert c_stats[0]["sum"] == pytest.approx(101.0)
    assert cost_stats[-1]["sum"] == pytest.approx(10.0 + 11 * 0.2)


# ------------------------------------------------------------------ periods

def test_generate_monthly_periods_boundaries():
    periods = C.generate_monthly_periods(
        datetime.date(2025, 11, 15), datetime.date(2026, 2, 10), 4.6, 3.3
    )
    assert [p["start_date"] for p in periods] == [
        datetime.date(2025, 11, 1),
        datetime.date(2025, 12, 1),
        datetime.date(2026, 1, 1),
        datetime.date(2026, 2, 1),
    ]
    assert periods[1]["end_date"] == datetime.date(2025, 12, 31)  # year rollover
    assert periods[-1]["end_date"] == datetime.date(2026, 2, 10)  # clipped to end
    assert periods[0]["power_high"] == 4.6 and periods[0]["power_low"] == 3.3


def test_billing_periods_csv_roundtrip(tmp_path):
    path = str(tmp_path / "billing_periods.csv")
    periods = [
        {
            "start_date": datetime.date(2026, 1, 1),
            "end_date": datetime.date(2026, 1, 31),
            "power_high": 4.6,
            "power_low": 4.6,
            "total_cost": 54.32,
            "total_consumption": 200.0,
            "power_cost": 10.0,
            "energy_cost": 40.0,
            "rent_cost": 2.0,
            "tax_cost": 2.32,
        },
        {
            "start_date": datetime.date(2026, 2, 1),
            "end_date": datetime.date(2026, 2, 28),
            "power_high": 4.6,
            "power_low": 4.6,
        },
    ]
    C.save_billing_periods(path, periods)
    loaded = C.load_billing_periods(path)
    assert loaded[0]["total_cost"] == pytest.approx(54.32)
    assert loaded[0]["start_date"] == datetime.date(2026, 1, 1)
    assert loaded[1]["total_cost"] == ""  # empty cell kept as "" → treated as uncalled


def test_load_billing_periods_skips_bad_rows(tmp_path, caplog):
    path = tmp_path / "billing_periods.csv"
    path.write_text(
        "start_date,end_date,power_high,power_low,total_cost,total_consumption,"
        "power_cost,energy_cost,rent_cost,tax_cost\n"
        "garbage,line,here,x,x,x,x,x,x,x\n"
        "2026-01-01,2026-01-31,4.6,4.6,,,,,,\n"
    )
    periods = C.load_billing_periods(str(path))
    assert len(periods) == 1
    assert periods[0]["start_date"] == datetime.date(2026, 1, 1)


# ------------------------------------------------------------------ timestamps

def test_madrid_timestamp_independent_of_host_tz():
    # Naive datetime interpreted as Madrid time, matching zoneinfo rules.
    t = madrid_timestamp(datetime.datetime(2026, 7, 1, 12))
    assert datetime.datetime.fromtimestamp(t, MADRID_TZ).hour == 12
    d = datetime.date(2026, 1, 1)
    assert datetime.datetime.fromtimestamp(madrid_timestamp(d), MADRID_TZ).hour == 0
