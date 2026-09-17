"""Regression tests: no duplicate fetches per cycle, no needless state writes."""
import datetime

import pytest

from custom_components.enchufado import coordinator as coord_mod
from custom_components.enchufado.coordinator import EnchufadoCoordinator as C
from custom_components.enchufado.const import CURRENT_BILL_STATE
from custom_components.enchufado.util import madrid_timestamp, madrid_today

pytestmark = pytest.mark.timeout(60)


class _State:
    def __init__(self, state, attributes):
        self.state = state
        self.attributes = attributes


class StatesStub:
    def __init__(self):
        self._states = {}
        self.set_count = 0

    def get(self, entity_id):
        return self._states.get(entity_id)

    def async_set(self, entity_id, state, attributes=None):
        self.set_count += 1
        self._states[entity_id] = _State(state, dict(attributes or {}))


class HassStub:
    """Minimal hass surface for coordinator methods (executor + states)."""

    class _Config:
        def __init__(self, p):
            self._p = p

        def path(self, *_):
            return self._p

    def __init__(self, tmp_path):
        self.states = StatesStub()
        self.config = HassStub._Config(str(tmp_path))

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _configure(tmp_path):
    C.user_files_path = str(tmp_path)
    C.energy_file = str(tmp_path / "energy_data.csv")
    C.billing_periods_file = str(tmp_path / "billing_periods.csv")
    C.power_high = 4.6
    C.power_low = 4.6
    C.esios_token = None
    C.cups = "CUPS"
    C.zip_code = "28001"


async def test_import_skips_datadis_and_writes_when_up_to_date(tmp_path, monkeypatch):
    """A cycle over fully cached data must not hit Datadis nor rewrite files."""
    _configure(tmp_path)
    end = madrid_today() - datetime.timedelta(days=2)
    start = end - datetime.timedelta(days=40)

    lines = ["date,timestamp,consumption,price,reading_type"]
    day = start
    while day <= end:
        for h in range(24):
            t = madrid_timestamp(datetime.datetime(day.year, day.month, day.day, h))
            date_s = f"{day.day:02d}/{day.month:02d}/{day.year} {h:02d}"
            lines.append(f"{date_s},{t},0.4,0.1,R")
        day += datetime.timedelta(days=1)
    (tmp_path / "energy_data.csv").write_text("\n".join(lines) + "\n")

    stats_inserts = []

    async def no_datadis(*a, **k):
        stats_inserts.append("datadis")  # must never happen
        return {}

    async def stub_bill(period, *a, **k):
        period["total_cost"] = 10.0
        return period, None

    saved = {"count": 0}
    real_save = C._write_energy_file

    def counting_save(*args):
        saved["count"] += 1
        return real_save(*args)

    monkeypatch.setattr(coord_mod.Datadis, "consumptions", no_datadis)
    monkeypatch.setattr(coord_mod, "calculate_bill", stub_bill)
    monkeypatch.setattr(C, "_write_energy_file", counting_save)
    monkeypatch.setattr(
        coord_mod, "async_add_external_statistics",
        lambda hass, metadata, stats: stats_inserts.append((metadata["statistic_id"], len(stats))),
    )

    class RegistryStub:
        @staticmethod
        def async_get_entity_id(*a):
            return None

    monkeypatch.setattr(coord_mod.er, "async_get", lambda hass: RegistryStub())

    hass = HassStub(tmp_path)
    await C.import_energy_data(hass)

    assert "datadis" not in stats_inserts  # already up to date → no Datadis request
    assert saved["count"] == 0  # nothing changed → energy file untouched
    # Only the bill statistic is inserted (one point per billed period);
    # consumption/cost statistics are NOT re-inserted on an unchanged cycle.
    ids = {i[0] for i in stats_inserts if isinstance(i, tuple)}
    assert coord_mod.CONSUMPTION_STATISTIC_ID not in ids
    assert coord_mod.COST_STATISTIC_ID not in ids


async def test_calculate_bills_skips_state_write_when_unchanged(tmp_path, monkeypatch):
    _configure(tmp_path)
    today = madrid_today()
    period = {
        "start_date": today.replace(day=1) - datetime.timedelta(days=1),
        "end_date": today.replace(day=1) - datetime.timedelta(days=1),
        "power_high": 4.6,
        "power_low": 4.6,
        "total_cost": 42.0,
        "total_consumption": 100.0,
        "power_cost": 10.0,
        "energy_cost": 30.0,
        "rent_cost": 1.0,
        "tax_cost": 1.0,
    }
    consumptions = {madrid_timestamp(period["start_date"]): {"value": 5.0, "reading_type": "R"}}

    async def never_bill(*a, **k):  # nothing to recompute
        raise AssertionError("calculate_bill must not be called when cost is cached")

    monkeypatch.setattr(coord_mod, "calculate_bill", never_bill)

    class RegistryStub:
        @staticmethod
        def async_get_entity_id(*a):
            return None

    monkeypatch.setattr(coord_mod.er, "async_get", lambda hass: RegistryStub())
    monkeypatch.setattr(coord_mod, "async_add_external_statistics", lambda *a, **k: None)
    hass = HassStub(tmp_path)

    await C.calculate_bills(hass, [dict(period)], consumptions)
    assert hass.states.set_count == 1
    published = hass.states.get(CURRENT_BILL_STATE)
    assert published is not None and published.state == "42.00 €"

    await C.calculate_bills(hass, [dict(period)], consumptions)
    assert hass.states.set_count == 1  # unchanged → no second write

    await C.calculate_bills(hass, [dict(period, total_cost=43.5)], consumptions)
    assert hass.states.set_count == 2  # changed → written again


async def test_get_data_skips_fully_cached_ranges():
    """Chunked price fetching must not re-request days already cached."""
    calls = []

    async def getter(start, end):
        calls.append((start, end))
        return {}

    end = datetime.date(2026, 5, 30)
    cached = {
        madrid_timestamp(datetime.datetime(2026, 5, d)): 0.1 for d in range(1, 31)
    }
    await C.get_data(getter, datetime.date(2026, 5, 1), end, cached, 28)
    assert calls == []


async def test_get_data_fetches_only_the_missing_tail():
    """Only days after the cached horizon are requested, in <=28d chunks."""
    calls = []

    async def getter(start, end):
        calls.append((start, end))
        day = start
        out = {}
        while day <= end:
            out[madrid_timestamp(datetime.datetime(day.year, day.month, day.day))] = 0.2
            day += datetime.timedelta(days=1)
        return out

    cached = {
        madrid_timestamp(datetime.datetime(2026, 5, d)): 0.1 for d in range(1, 26)
    }
    await C.get_data(getter, datetime.date(2026, 5, 1), datetime.date(2026, 5, 30), cached, 28)
    assert calls == [(datetime.date(2026, 5, 26), datetime.date(2026, 5, 30))]
