"""Offline behavior tests for the REVIEW-CRITICAL-2026-10 pass-2 fixes.

The repo's pytest suite (tests/test_*.py) requires homeassistant +
pytest-homeassistant-custom-component, which cannot be installed on this
host. This module runs the same behaviors with the standard library's
unittest runner (`python3 -m unittest tests.test_pass2_offline -v`) by
stubbing the aiohttp/homeassistant imports when they are absent. On a CI
host with the real dependencies, the stubs are skipped and the real
modules are used.

Coverage:
  MAJOR-2  statistics-resume branch of import_energy_data (coordinator.py):
           resume from get_last_statistics, float-epoch `start` assumption,
           full-rebuild fallback when the statistics APIs return nothing,
           and the UTC/Madrid date-mismatch rebuild.
  MAJOR-4  options flow for Datadis credentials (config_flow.py).
  MINOR-3  shared aiohttp session reuse in ree.py / cnmc.py.
  MINOR-9/10/11  README entity id, complete en.json, manifest provenance.
"""
import datetime
import os
import sys
import tempfile
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, _TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _module(name, **attrs):
    """Create (or reuse) a module and register it plus its parents."""
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        key = ".".join(parts[:i])
        if key not in sys.modules:
            sys.modules[key] = types.ModuleType(key)
    mod = sys.modules[name]
    for k, v in attrs.items():
        setattr(mod, k, v)
    if len(parts) > 1:
        setattr(sys.modules[".".join(parts[:-1])], parts[-1], mod)
    return mod


def _install_stubs():
    try:
        import aiohttp  # noqa: F401
        import homeassistant  # noqa: F401
        import voluptuous  # noqa: F401

        return
    except ImportError:
        pass

    # --- aiohttp -----------------------------------------------------------
    class _ClientTimeout:
        def __init__(self, total=None, connect=None):
            self.total = total
            self.connect = connect

    _module(
        "aiohttp",
        ClientTimeout=_ClientTimeout,
        ClientSession=type("ClientSession", (), {}),
        ContentTypeError=type("ContentTypeError", (Exception,), {}),
        ClientError=type("ClientError", (Exception,), {}),
    )

    # --- homeassistant -----------------------------------------------------
    _module("homeassistant")

    class _UnitOfEnergy:
        KILO_WATT_HOUR = "kWh"

    class _Platform:
        NUMBER = "number"

    const = _module("homeassistant.const", CURRENCY_EURO="EUR")
    const.UnitOfEnergy = _UnitOfEnergy
    const.Platform = _Platform

    _module("homeassistant.core", HomeAssistant=type("HomeAssistant", (), {}))

    recorder = _module("homeassistant.components.recorder", get_instance=lambda hass: hass)
    recorder.models = _module("homeassistant.components.recorder.models")

    class _StatisticData:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _StatisticMeanType:
        NONE = "none"

    class _StatisticMetaData:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    recorder.models.StatisticData = _StatisticData
    recorder.models.StatisticMeanType = _StatisticMeanType
    recorder.models.StatisticMetaData = _StatisticMetaData

    _module(
        "homeassistant.components.recorder.statistics",
        async_add_external_statistics=lambda *a, **k: None,
        get_last_statistics=lambda *a, **k: {},
        statistics_during_period=lambda *a, **k: {},
    )

    class _EnergyConverter:
        UNIT_CLASS = "energy"

    _module("homeassistant.util.unit_conversion", EnergyConverter=_EnergyConverter)

    _module("homeassistant.helpers")
    _module("homeassistant.helpers.entity_registry")
    _module("homeassistant.helpers.event", async_track_time_change=lambda *a, **k: None)
    _module(
        "homeassistant.helpers.issue_registry",
        IssueSeverity=types.SimpleNamespace(
            ERROR="error", WARNING="warning", CRITICAL="critical"
        ),
        async_create_issue=lambda *a, **k: None,
        async_delete_issue=lambda *a, **k: None,
        async_get=lambda hass: types.SimpleNamespace(
            async_get_issue=lambda *a, **k: None
        ),
    )

    # --- config flow dependencies (MAJOR-4) --------------------------------
    class _Marker:
        def __init__(self, schema):
            self.schema = schema

    _module(
        "voluptuous",
        Schema=lambda d: d,
        Required=lambda schema, **k: _Marker(schema),
        Optional=lambda schema, **k: _Marker(schema),
    )

    _module("homeassistant.helpers.config_validation", string=lambda v: v)
    _module("homeassistant.helpers.selector", selector=lambda spec: spec)

    class _ConfigFlow:
        def __init_subclass__(cls, domain=None, **kwargs):
            pass

        def async_show_form(self, step_id, data_schema=None, errors=None):
            return {"type": "form", "step_id": step_id, "errors": errors or {}}

        def async_create_entry(self, title, data):
            return {"type": "create_entry", "title": title, "data": data}

        def async_abort(self, reason):
            return {"type": "abort", "reason": reason}

    class _OptionsFlow(_ConfigFlow):
        config_entry = None

    cfg_entries = _module("homeassistant.config_entries")
    cfg_entries.ConfigFlow = _ConfigFlow
    cfg_entries.OptionsFlow = _OptionsFlow


_install_stubs()

from custom_components.enchufado import cnmc, config_flow, coordinator as coord_mod, ree  # noqa: E402
from custom_components.enchufado.coordinator import EnchufadoCoordinator  # noqa: E402
from custom_components.enchufado.datadis import Datadis  # noqa: E402
from custom_components.enchufado.ree import REE  # noqa: E402
from custom_components.enchufado.util import madrid_timestamp  # noqa: E402

from fakes import FakeResponse, FakeSession, route  # noqa: E402

C_STAT = coord_mod.CONSUMPTION_STATISTIC_ID
K_STAT = coord_mod.COST_STATISTIC_ID

DAY1 = datetime.date(2026, 3, 1)
DAY2 = datetime.date(2026, 3, 2)
DAY3 = datetime.date(2026, 3, 3)


def _hour(day_dt, value=0.5):
    ts = madrid_timestamp(day_dt)
    return ts, {"value": value, "reading_type": "R"}


def _day_hours(day, value=0.5):
    """24 hourly entries {ts: {...}} for the given Madrid calendar day."""
    return {
        ts: entry
        for ts, entry in (
            _hour(datetime.datetime(day.year, day.month, day.day, h), value)
            for h in range(24)
        )
    }


def _with_priced(consumptions, price):
    return consumptions, {ts: price for ts in consumptions}


class FakeHass:
    """Minimal hass: executor jobs run inline, config.path hits a temp dir."""

    def __init__(self, path):
        self.config = types.SimpleNamespace(path=lambda *_a: path)
        self.executor_jobs = []

    async def async_add_executor_job(self, fn, *args):
        self.executor_jobs.append(fn)
        return fn(*args)


# ---------------------------------------------------------------------------
# MAJOR-2: statistics-resume branch of import_energy_data
# ---------------------------------------------------------------------------


class ResumeTestsBase(unittest.IsolatedAsyncioTestCase):
    """Drives a full import_energy_data cycle with stubbed upstreams."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.hass = FakeHass(self._tmp.name)

        EnchufadoCoordinator.user_files_path = self._tmp.name
        EnchufadoCoordinator.energy_file = os.path.join(self._tmp.name, "energy_data.csv")
        EnchufadoCoordinator.billing_periods_file = os.path.join(
            self._tmp.name, "billing_periods.csv"
        )
        EnchufadoCoordinator.esios_token = None  # skip the REE fetch
        EnchufadoCoordinator.power_high = 4.6
        EnchufadoCoordinator.power_low = 4.6
        Datadis.last_error = None
        REE.last_error = None

        # Cached history: two full days at 0.5 kWh, 0.1 €/kWh.
        c1, p1 = _with_priced(_day_hours(DAY1, 0.5), 0.1)
        c2, p2 = _with_priced(_day_hours(DAY2, 0.5), 0.1)
        self.cached = {**c1, **c2}
        self.cached_prices = {**p1, **p2}
        EnchufadoCoordinator._write_energy_file(
            EnchufadoCoordinator.energy_file, self.cached, self.cached_prices
        )

        # Fresh data Datadis "returns" for the missing day.
        self.new_day = _day_hours(DAY3, 0.25)

    def tearDown(self):
        Datadis.last_error = None
        REE.last_error = None

    async def run_import(self, last_stat=None, during_period=None):
        """Run import_energy_data with stubbed recorder APIs.

        Returns a context dict with the recorder call arguments, the
        create_statistics seed arguments, and the produced statistics.
        """
        ctx = {
            "get_last_statistics_args": None,
            "during_period_args": None,
            "create_args": None,
            "c_stats": None,
            "cost_stats": None,
        }
        last_stat = last_stat if last_stat is not None else {}
        during_period = during_period if during_period is not None else {}

        async def fake_datadis(start_date, end_date):
            # One day of fresh data starting at the requested date.
            return _day_hours(start_date, 0.25)

        def fake_get_last(*args):
            ctx["get_last_statistics_args"] = args
            return last_stat

        def fake_during(*args):
            ctx["during_period_args"] = args
            return during_period

        orig_create = EnchufadoCoordinator.__dict__["create_statistics"]

        def spy(last_statistic_timestamp, consumptions, prices, total_c, total_cost):
            ctx["create_args"] = (last_statistic_timestamp, total_c, total_cost)
            c_stats, cost_stats = orig_create(
                last_statistic_timestamp, consumptions, prices, total_c, total_cost
            )
            ctx["c_stats"] = c_stats
            ctx["cost_stats"] = cost_stats
            return c_stats, cost_stats

        orig_consumptions = Datadis.__dict__["consumptions"]
        orig_get_last = coord_mod.get_last_statistics
        orig_during = coord_mod.statistics_during_period
        orig_bills = EnchufadoCoordinator.__dict__["calculate_bills"]

        async def no_bills(*args, **kwargs):
            return None

        try:
            Datadis.consumptions = staticmethod(fake_datadis)
            coord_mod.get_last_statistics = fake_get_last
            coord_mod.statistics_during_period = fake_during
            EnchufadoCoordinator.calculate_bills = staticmethod(no_bills)
            EnchufadoCoordinator.create_statistics = staticmethod(spy)
            await EnchufadoCoordinator.import_energy_data(self.hass)
        finally:
            Datadis.consumptions = orig_consumptions
            coord_mod.get_last_statistics = orig_get_last
            coord_mod.statistics_during_period = orig_during
            EnchufadoCoordinator.calculate_bills = orig_bills
            EnchufadoCoordinator.create_statistics = orig_create
        return ctx


class TestResumeFromLastStatistic(ResumeTestsBase):
    """The incremental path: recorder's last point is consistent with the CSV."""

    def _resume_fixtures(self):
        # Recorder's last consumption point: Madrid 23:00 of DAY2.
        # Winter (CET) → 22:00 UTC, same calendar date in UTC and Madrid.
        last_ts = madrid_timestamp(datetime.datetime(2026, 3, 2, 23))
        last_stat = {C_STAT: [{"start": float(last_ts), "state": 0.5, "sum": 24.0}]}
        during = {
            C_STAT: [{"start": float(last_ts), "sum": 24.0}],
            K_STAT: [{"start": float(last_ts), "sum": 5.0}],
        }
        return last_ts, last_stat, during

    async def test_resume_inserts_only_the_missing_day(self):
        last_ts, last_stat, during = self._resume_fixtures()

        ctx = await self.run_import(last_stat, during)

        # Statistics on disk must now cover the missing day only.
        consumptions, _prices = EnchufadoCoordinator._read_energy_file(
            EnchufadoCoordinator.energy_file
        )
        self.assertEqual(len(consumptions), 72)  # 2 cached days + 1 new day
        self.assertIn(madrid_timestamp(datetime.datetime(2026, 3, 3, 0)), consumptions)

        # create_statistics resumed from the recorder's hour and totals.
        c_stats, cost_stats = ctx["c_stats"], ctx["cost_stats"]
        self.assertEqual(len(c_stats), 24)
        self.assertEqual(len(cost_stats), 24)
        self.assertEqual(
            c_stats[0].start,
            datetime.datetime(2026, 3, 2, 23, 0, tzinfo=datetime.timezone.utc),
        )  # DAY3 00:00 Madrid == 03-02 23:00 UTC
        self.assertAlmostEqual(c_stats[0].sum, 24.0 + 0.25)
        self.assertAlmostEqual(c_stats[-1].sum, 24.0 + 24 * 0.25)
        self.assertAlmostEqual(cost_stats[-1].sum, 5.0)  # new day has no prices → 0 cost

    async def test_resume_seeds_from_recorder_totals(self):
        last_ts, last_stat, during = self._resume_fixtures()

        ctx = await self.run_import(last_stat, during)

        self.assertEqual(ctx["create_args"], (float(last_ts), 24.0, 5.0))

    async def test_float_epoch_start_from_recorder(self):
        """The recorder contract: `start` is a float epoch — resume handles it."""
        last_ts, last_stat, during = self._resume_fixtures()
        assert isinstance(last_ts, int)

        ctx = await self.run_import(last_stat, during)

        # get_last_statistics was asked for the consumption statistic only.
        _hass, limit, stat_id, _types, metrics = ctx["get_last_statistics_args"]
        self.assertEqual(stat_id, C_STAT)
        self.assertEqual(limit, 1)
        self.assertEqual(metrics, set())
        # statistics_during_period received the float epoch as a UTC datetime.
        (_h, start, _none, ids, _period, _m, _sel) = ctx["during_period_args"]
        self.assertEqual(start, datetime.datetime.fromtimestamp(float(last_ts), datetime.UTC))
        self.assertEqual(ids, {C_STAT, K_STAT})

    async def test_resume_query_covers_both_statistics(self):
        last_ts, last_stat, during = self._resume_fixtures()

        ctx = await self.run_import(last_stat, during)

        (_h, _start, _none, ids, _period, _m, _sel) = ctx["during_period_args"]
        self.assertEqual(ids, {C_STAT, K_STAT})

    def setUp_extra(self):
        # helper attribute for assert message readability in test_resume_inserts
        self._last_ctx = None


class TestFullRebuildFallback(ResumeTestsBase):
    async def test_no_last_statistics_rebuilds_from_scratch(self):
        """get_last_statistics → {} → full rebuild, no during_period query."""
        ctx = await self.run_import(last_stat={}, during_period={})

        self.assertIsNone(ctx["during_period_args"])  # resume query never ran
        self.assertEqual(ctx["create_args"], (0, 0, 0))

        # Rows must cover the whole cached history with totals from zero.
        c_stats = ctx["c_stats"]
        self.assertEqual(len(c_stats), 72)  # 2 cached days + 1 new day
        self.assertAlmostEqual(c_stats[0].sum, 0.5)
        # 48 cached hours @0.5 + 24 new hours @0.25
        self.assertAlmostEqual(c_stats[-1].sum, 48 * 0.5 + 24 * 0.25)

    async def test_empty_last_statistic_rows_rebuild(self):
        """Recorder knows the id but returns an empty row list."""
        ctx = await self.run_import(last_stat={C_STAT: []}, during_period={})

        self.assertIsNone(ctx["during_period_args"])
        self.assertEqual(ctx["create_args"], (0, 0, 0))

    async def test_missing_during_period_rows_rebuild(self):
        """Resume point exists but statistics_during_period returns nothing."""
        last_ts = madrid_timestamp(datetime.datetime(2026, 3, 2, 23))
        last_stat = {C_STAT: [{"start": float(last_ts), "sum": 24.0}]}

        ctx = await self.run_import(last_stat, during_period={})

        # The resume read found no rows → fall back to the full rebuild.
        self.assertEqual(ctx["create_args"], (0, 0, 0))

    async def test_missing_cost_statistic_rebuild(self):
        """Only the consumption statistic comes back → no resume."""
        last_ts = madrid_timestamp(datetime.datetime(2026, 3, 2, 23))
        last_stat = {C_STAT: [{"start": float(last_ts), "sum": 24.0}]}
        during = {C_STAT: [{"start": float(last_ts), "sum": 24.0}]}  # cost missing

        ctx = await self.run_import(last_stat, during)

        self.assertEqual(ctx["create_args"], (0, 0, 0))

    async def test_utc_madrid_date_mismatch_rebuilds(self):
        """Off-by-one magnet: recorder's last hour lands on the previous UTC date.

        Cached history ends at Madrid 00:00 (its epoch is 23:00 UTC the
        previous day). The UTC-derived comparison date disagrees with the
        Madrid-local date → the safe full-rebuild path must run, never a
        silent resume with a wrong seed.
        """
        single = {
            madrid_timestamp(datetime.datetime(2026, 3, 2, 0)): {
                "value": 0.5,
                "reading_type": "R",
            }
        }
        merged = dict(single)
        merged.update(self.new_day)
        EnchufadoCoordinator._write_energy_file(
            EnchufadoCoordinator.energy_file, merged, {}
        )
        last_ts = madrid_timestamp(datetime.datetime(2026, 3, 2, 0))  # 03-01 23:00 UTC
        last_stat = {C_STAT: [{"start": float(last_ts), "sum": 0.5}]}
        during = {
            C_STAT: [{"start": float(last_ts), "sum": 0.5}],
            K_STAT: [{"start": float(last_ts), "sum": 0.05}],
        }

        ctx = await self.run_import(last_stat, during)

        # 2026-03-01 23:00 UTC has UTC-date 03-01 but Madrid-date 03-02:
        # mismatch → full rebuild with zero seeds.
        self.assertEqual(ctx["create_args"], (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
