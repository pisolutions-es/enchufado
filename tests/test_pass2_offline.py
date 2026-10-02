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
import asyncio
import datetime
import json
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

    _module(
        "homeassistant.core",
        HomeAssistant=type("HomeAssistant", (), {}),
        callback=lambda f: f,
    )

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


# ---------------------------------------------------------------------------
# MAJOR-4: options flow for Datadis credentials
# ---------------------------------------------------------------------------
from custom_components.enchufado.const import (  # noqa: E402
    CONF_AUTHORIZED_NIF,
    CONF_CUPS,
    CONF_DATADIS_PASSWORD,
    CONF_DATADIS_USER,
    CONF_DISTRIBUTOR_CODE,
    CONF_ESIOS_TOKEN,
    CONF_POINT_TYPE,
)


def _entry_data():
    return {
        CONF_DATADIS_USER: "old-user",
        CONF_DATADIS_PASSWORD: "old-pass",
        CONF_AUTHORIZED_NIF: None,
        CONF_ESIOS_TOKEN: "old-token",
        CONF_CUPS: "ES0012345678901234567890",
        CONF_DISTRIBUTOR_CODE: "2",
        CONF_POINT_TYPE: 1,
    }


class OptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    def _flow(self, data=None):
        flow = config_flow.EnchufadoOptionsFlow()
        flow.config_entry = types.SimpleNamespace(data=data if data is not None else _entry_data())
        return flow

    def setUp(self):
        self._orig_login = config_flow.async_login
        self.login_calls = []

        async def fake_login(username, password):
            self.login_calls.append((username, password))
            return "tok" if password != "bad" else None

        config_flow.async_login = fake_login
        self.addCleanup(setattr, config_flow, "async_login", self._orig_login)

    async def test_reentered_credentials_are_saved(self):
        flow = self._flow()

        result = await flow.async_step_init(
            {CONF_DATADIS_USER: "new-user", CONF_DATADIS_PASSWORD: "new-pass"}
        )

        self.assertEqual(result["type"], "create_entry")
        data = result["data"]
        self.assertEqual(data[CONF_DATADIS_USER], "new-user")
        self.assertEqual(data[CONF_DATADIS_PASSWORD], "new-pass")
        # Keys not touched by the options flow survive untouched.
        self.assertEqual(data[CONF_CUPS], "ES0012345678901234567890")
        self.assertEqual(data[CONF_DISTRIBUTOR_CODE], "2")
        self.assertEqual(data[CONF_POINT_TYPE], 1)
        self.assertEqual(data[CONF_ESIOS_TOKEN], "old-token")
        # Changed credentials were validated against the API before saving.
        self.assertEqual(self.login_calls, [("new-user", "new-pass")])

    async def test_bad_credentials_show_error_and_keep_entry(self):
        flow = self._flow()

        result = await flow.async_step_init(
            {CONF_DATADIS_USER: "new-user", CONF_DATADIS_PASSWORD: "bad"}
        )

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        # The stored entry data was not touched.
        self.assertEqual(flow.config_entry.data[CONF_DATADIS_USER], "old-user")

    async def test_blank_fields_keep_stored_values_without_login(self):
        flow = self._flow()

        result = await flow.async_step_init({})

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], _entry_data())
        self.assertEqual(self.login_calls, [])  # nothing changed → no API call

    async def test_token_only_update_skips_datadis_login(self):
        flow = self._flow()

        result = await flow.async_step_init({CONF_ESIOS_TOKEN: "new-esios-token"})

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_ESIOS_TOKEN], "new-esios-token")
        self.assertEqual(self.login_calls, [])
        self.assertEqual(result["data"][CONF_DATADIS_PASSWORD], "old-pass")

    async def test_flow_registered_on_config_flow(self):
        self.assertTrue(hasattr(config_flow.EnchufadoConfigFlow, "async_get_options_flow"))
        flow = config_flow.EnchufadoConfigFlow.async_get_options_flow(None)
        self.assertIsInstance(flow, config_flow.EnchufadoOptionsFlow)


# ---------------------------------------------------------------------------
# MINOR-10: complete i18n (en.json) — strings.json is the English source
# ---------------------------------------------------------------------------
_COMPONENT = os.path.join(_ROOT, "custom_components", "enchufado")


def _load_json(rel):
    with open(os.path.join(_COMPONENT, rel), encoding="utf-8") as f:
        return json.load(f)


def _all_keys(node, prefix=()):
    """Every leaf path of a nested dict as tuples."""
    for k, v in node.items():
        if isinstance(v, dict):
            yield from _all_keys(v, prefix + (k,))
        else:
            yield prefix + (k,)


class I18nTests(unittest.TestCase):
    def setUp(self):
        self.strings = _load_json("strings.json")
        self.en = _load_json(os.path.join("translations", "en.json"))
        self.es = _load_json(os.path.join("translations", "es.json"))

    def test_files_are_valid_json(self):
        self.assertIsInstance(self.strings, dict)
        self.assertIsInstance(self.en, dict)
        self.assertIsInstance(self.es, dict)

    def test_en_json_covers_every_strings_key(self):
        missing = set(_all_keys(self.strings)) - set(_all_keys(self.en))
        self.assertEqual(missing, set())

    def test_es_json_covers_every_strings_key(self):
        missing = set(_all_keys(self.strings)) - set(_all_keys(self.es))
        self.assertEqual(missing, set())

    def test_config_flow_is_english_in_strings_json(self):
        # MINOR-10: the setup wizard must not be Spanish-only in the source.
        step = self.strings["config"]["step"]["user"]
        self.assertNotIn("Contraseña", json.dumps(step))

    def test_options_flow_strings_exist(self):
        for lang, name in ((self.strings, "strings"), (self.en, "en"), (self.es, "es")):
            self.assertIn("init", lang["options"]["step"], name)
            self.assertIn("cannot_connect", lang["options"]["error"], name)

    def test_repair_copy_points_at_the_options_flow(self):
        # MAJOR-4 follow-through: no more dead-end instructions.
        for lang in (self.strings, self.en, self.es):
            desc = lang["issues"]["datadis_auth_failed"]["description"]
            self.assertTrue("Configure" in desc or "Configurar" in desc, desc)
            self.assertNotIn("re-add the integration", desc)
            self.assertNotIn("volviendo a añadir la integración", desc)


# ---------------------------------------------------------------------------
# MINOR-3: shared aiohttp session reuse in ree.py / cnmc.py
# ---------------------------------------------------------------------------
class _SessionManagerStub:
    """Replaces aiohttp.ClientSession(...) — counts instantiations."""

    def __init__(self, session):
        self._session = session
        self.created = 0
        self.timeouts = []

    def __call__(self, *args, **kwargs):
        self.created += 1
        if "timeout" in kwargs:
            self.timeouts.append(kwargs["timeout"])
        return self._session


def _install_session_manager(module):
    """Point module.aiohttp.ClientSession at a stub; returns (manager, restore)."""
    manager = _SessionManagerStub(FakeSession([], []))
    orig = module.aiohttp.ClientSession
    module.aiohttp.ClientSession = manager

    def restore():
        module.aiohttp.ClientSession = orig

    return manager, restore


def _ree_routes():
    """A minimal valid ESIOS payload routed by method+regex."""
    payload = {"indicator": {"values": [{"datetime": "2026-03-01T00:00:00+01:00", "value": 100.0}]}}
    return [route("GET", r"esios\.ree\.es.*", FakeResponse(200, json_data=payload))]


def _cnmc_fixtures():
    period = {
        "start_date": DAY1,
        "end_date": DAY2,
        "power_high": 4.6,
        "power_low": 4.6,
    }
    consumptions = {ts: 0.5 for ts in (*_day_hours(DAY1, 0.5), *_day_hours(DAY2, 0.5))}
    bill_payload = {
        "graficoGastoTotalActual": {
            "importeTotal": 50.0,
            "importePotencia": 10.0,
            "importeEnergia": 30.0,
            "importeAlquiler": 1.0,
            "importeIVA": 9.0,
        },
        "graficaConsumoDiario": {
            "consumosDiarios": [{"fecha": "01/03/2026"}, {"fecha": "02/03/2026"}]
        },
    }
    return period, consumptions, bill_payload


class SessionReuseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._restores = []

    def tearDown(self):
        for restore in self._restores:
            restore()
        # Drop the module-level sessions between tests.
        asyncio.run(ree.close_session())
        asyncio.run(cnmc.close_session())

    def test_ree_reuses_shared_session(self):
        manager, restore = _install_session_manager(ree)
        self._restores.append(restore)
        manager._session._routes = _ree_routes()

        async def twice():
            await REE.pvpc(DAY1, DAY1, "tok")
            await REE.pvpc(DAY2, DAY2, "tok")

        asyncio.run(twice())
        self.assertEqual(manager.created, 1)  # one session, two requests
        # The per-request timeout still rides on every call.
        calls = manager._session.calls
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIs(call["kwargs"]["timeout"], ree._TIMEOUT)

    async def test_ree_shared_session_survives_and_closes(self):
        manager, restore = _install_session_manager(ree)
        self._restores.append(restore)
        manager._session._routes = _ree_routes()

        await REE.pvpc(DAY1, DAY1, "tok")
        await ree.close_session()
        await REE.pvpc(DAY2, DAY2, "tok")

        self.assertEqual(manager.created, 2)  # closed → a fresh session is built

    async def test_ree_parses_prices_over_shared_session(self):
        manager, restore = _install_session_manager(ree)
        self._restores.append(restore)

        manager._session._routes = _ree_routes()

        prices = await REE.pvpc(DAY1, DAY1, "tok")

        self.assertEqual(prices, {madrid_timestamp(DAY1): 0.1})  # MWh → €/kWh
        self.assertIsNone(REE.last_error)

    async def test_cnmc_reuses_shared_session(self):
        manager, restore = _install_session_manager(cnmc)
        self._restores.append(restore)
        period, consumptions, bill_payload = _cnmc_fixtures()
        manager._session._routes = [
            route("POST", r"comparador\.cnmc\.gob\.es.*cargar.*", FakeResponse(200, text="file123-rest")),
            route("GET", r"comparador\.cnmc\.gob\.es.*ofertas.*", FakeResponse(200, json_data=bill_payload)),
        ]

        async def twice():
            p1 = dict(period)
            await cnmc.calculate_bill(p1, "CUPS", consumptions, "28001")
            p2 = dict(period)
            await cnmc.calculate_bill(p2, "CUPS", consumptions, "28001")
            return p1, p2

        p1, p2 = await twice()

        self.assertEqual(manager.created, 1)  # one session for both bill calls
        # 2 uploads + 2 bill requests, each carrying the per-request timeout.
        calls = manager._session.calls
        self.assertEqual(len(calls), 4)
        for call in calls:
            self.assertIs(call["kwargs"]["timeout"], cnmc._TIMEOUT)
        self.assertEqual(p1["total_cost"], 50.0)  # happy path still works
        self.assertEqual(p2["total_cost"], 50.0)


if __name__ == "__main__":
    unittest.main()
