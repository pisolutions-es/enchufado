"""Offline behavior tests for the REVIEW-CRITICAL-2026-10 fixes.

The repo's pytest suite (tests/test_*.py) requires homeassistant +
pytest-homeassistant-custom-component, which cannot be installed on this
host. This module runs the same behaviors with the standard library's
unittest runner (`python3 -m unittest tests.test_fixes_offline -v`) by
stubbing the aiohttp/homeassistant imports when they are absent. On a CI
host with the real dependencies, the stubs are skipped and the real
modules are used.
"""
import asyncio
import concurrent.futures
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

    recorder = _module(
        "homeassistant.components.recorder", get_instance=lambda hass: hass
    )
    recorder.models = _module(
        "homeassistant.components.recorder.models"
    )

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

    _module(
        "homeassistant.util.unit_conversion", EnergyConverter=_EnergyConverter
    )

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


_install_stubs()

from custom_components.enchufado import coordinator as coord_mod  # noqa: E402
from custom_components.enchufado import datadis  # noqa: E402
from custom_components.enchufado.coordinator import EnchufadoCoordinator  # noqa: E402
from custom_components.enchufado.datadis import (  # noqa: E402
    Datadis,
    _retry_delay,
    async_login,
)
from custom_components.enchufado.ree import REE  # noqa: E402
from custom_components.enchufado.util import madrid_timestamp  # noqa: E402

from fakes import FakeResponse, FakeSession, route  # noqa: E402


def _hour(ts_dt, value=0.5):
    ts = madrid_timestamp(ts_dt)
    return ts, {"value": value, "reading_type": "R"}


# ---------------------------------------------------------------------------
# CRITICAL-1: bounded retries
# ---------------------------------------------------------------------------
class RetryBoundedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Datadis.setup("user", "pass", "ES-CUPS", "2", 1)
        Datadis._token = "tok"
        datadis._quota_blocked_until = 0.0
        self._orig_sleep = datadis._sleep

        async def no_sleep(_seconds):
            return None

        datadis._sleep = no_sleep

    def tearDown(self):
        datadis._sleep = self._orig_sleep
        datadis._session = None
        datadis._quota_blocked_until = 0.0
        Datadis.last_error = None

    def _fake_session(self, status, retry_after):
        calls = []
        handler = lambda **_: FakeResponse(  # noqa: E731
            status, text="body", headers={"Retry-After": retry_after} if retry_after else {}
        )
        session = FakeSession([route("GET", r".*", handler)], calls)
        session.post = session.get
        return session, calls

    def test_retry_after_capped_by_attempt_budget(self):
        self.assertEqual(_retry_delay(0, "60"), 60.0)
        self.assertEqual(_retry_delay(1, "86400"), 3600.0)  # 1 h clamp
        self.assertIsNone(_retry_delay(datadis._MAX_RETRIES, "60"))
        self.assertIsNone(_retry_delay(datadis._MAX_RETRIES + 5, "60"))

    def test_backoff_branch_still_capped(self):
        self.assertIsNone(_retry_delay(datadis._MAX_RETRIES, None))
        self.assertLessEqual(_retry_delay(0, None), 1.5)

    async def test_persistent_429_with_retry_after_terminates(self):
        session, calls = self._fake_session(429, "60")
        datadis._session = session

        d = datetime.date(2026, 3, 1)
        result = await Datadis.consumptions(d, d)

        self.assertEqual(result, {})
        self.assertEqual(Datadis.last_error, "quota")
        self.assertEqual(len(calls), datadis._MAX_RETRIES + 1)

    async def test_persistent_500_with_retry_after_terminates(self):
        session, calls = self._fake_session(503, "30")
        datadis._session = session

        d = datetime.date(2026, 3, 1)
        result = await Datadis.consumptions(d, d)

        self.assertEqual(result, {})
        self.assertEqual(Datadis.last_error, "network")
        self.assertEqual(len(calls), datadis._MAX_RETRIES + 1)

    async def test_login_with_persistent_429_terminates(self):
        calls = []
        handler = lambda **_: FakeResponse(  # noqa: E731
            429, text="slow down", headers={"Retry-After": "60"}
        )
        session = FakeSession([route("POST", r".*", handler)], calls)
        session.post = session.get  # both hit the same handler
        datadis._session = session

        token = await async_login("user", "pass")

        self.assertIsNone(token)
        self.assertEqual(len(calls), datadis._MAX_RETRIES + 1)


# ---------------------------------------------------------------------------
# MAJOR-1: atomic write + tolerant parsing
# ---------------------------------------------------------------------------
class _Boom:
    def __str__(self):
        raise RuntimeError("simulated crash mid-write")


class CsvRobustnessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "energy_data.csv")

    def test_successful_write_no_tmp_left(self):
        EnchufadoCoordinator._write_energy_file(
            self.path, dict([_hour(datetime.datetime(2026, 3, 1, 0))]), {}
        )
        with open(self.path) as f:
            body = f.read()
        self.assertIn("date,timestamp,consumption,price,reading_type", body)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_crash_mid_write_leaves_previous_file_intact(self):
        good_hours = [_hour(datetime.datetime(2026, 3, 1, h)) for h in range(24)]
        EnchufadoCoordinator._write_energy_file(self.path, dict(good_hours), {})
        with open(self.path, "rb") as f:
            good_bytes = f.read()

        ts, entry = _hour(datetime.datetime(2026, 3, 2, 0))
        consumptions = dict(good_hours)
        consumptions[ts] = entry
        with self.assertRaises(RuntimeError):
            EnchufadoCoordinator._write_energy_file(self.path, consumptions, {ts: _Boom()})

        # The old history survived byte-for-byte; the torn data is only in tmp.
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), good_bytes)
        self.assertTrue(os.path.exists(self.path + ".tmp"))

    def test_successful_rewrite_recovers_after_crashed_write(self):
        with self.assertRaises(RuntimeError):
            EnchufadoCoordinator._write_energy_file(self.path, {}, {0: _Boom()})

        EnchufadoCoordinator._write_energy_file(
            self.path, dict([_hour(datetime.datetime(2026, 3, 1, 0))]), {}
        )
        self.assertTrue(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        consumptions, prices = EnchufadoCoordinator._read_energy_file(self.path)
        ts = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
        self.assertEqual(consumptions, {ts: {"value": 0.5, "reading_type": "R"}})
        self.assertEqual(prices, {})

    def test_corrupted_line_is_skipped_not_fatal(self):
        ts0 = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
        ts1 = madrid_timestamp(datetime.datetime(2026, 3, 1, 1))
        ts2 = madrid_timestamp(datetime.datetime(2026, 3, 1, 2))
        with open(self.path, "w") as f:
            f.write("date,timestamp,consumption,price,reading_type\n")
            f.write(f"01/03/2026 0,{ts0},0.5,0.1,R\n")
            f.write("GARBAGE LINE,,$$\n")  # corrupted
            f.write(f"01/03/2026 2,{ts2},0.9,0.3,R\n")
            f.write(f"{ts1}\n")  # truncated final line

        consumptions, prices = EnchufadoCoordinator._read_energy_file(self.path)

        self.assertEqual(
            consumptions,
            {ts0: {"value": 0.5, "reading_type": "R"}, ts2: {"value": 0.9, "reading_type": "R"}},
        )
        self.assertEqual(prices, {ts0: 0.1, ts2: 0.3})

    def test_blank_line_is_skipped(self):
        ts0 = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
        with open(self.path, "w") as f:
            f.write("date,timestamp,consumption,price,reading_type\n")
            f.write(f"01/03/2026 0,{ts0},0.5,0.1,R\n")
            f.write("\n")

        consumptions, prices = EnchufadoCoordinator._read_energy_file(self.path)
        self.assertEqual(consumptions, {ts0: {"value": 0.5, "reading_type": "R"}})
        self.assertEqual(prices, {ts0: 0.1})

    def test_bad_float_only_drops_its_field(self):
        ts0 = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
        ts1 = madrid_timestamp(datetime.datetime(2026, 3, 1, 1))
        with open(self.path, "w") as f:
            f.write("date,timestamp,consumption,price,reading_type\n")
            f.write(f"01/03/2026 0,{ts0},0.5,0.1,R\n")
            f.write(f"01/03/2026 1,{ts1},0.5,not-a-float,R\n")

        consumptions, prices = EnchufadoCoordinator._read_energy_file(self.path)
        self.assertIn(ts1, consumptions)
        self.assertNotIn(ts1, prices)


# ---------------------------------------------------------------------------
# MAJOR-3: esios_token_missing wiring
# ---------------------------------------------------------------------------
_CONF_KEYS = coord_mod.__dict__


def _config(token):
    return {
        _CONF_KEYS["CONF_DATADIS_USER"]: "u",
        _CONF_KEYS["CONF_DATADIS_PASSWORD"]: "p",
        _CONF_KEYS["CONF_CUPS"]: "CUPS",
        _CONF_KEYS["CONF_DISTRIBUTOR_CODE"]: "2",
        _CONF_KEYS["CONF_POINT_TYPE"]: 1,
        _CONF_KEYS["CONF_ESIOS_TOKEN"]: token,
    }


class _FakeHass:
    """Minimal hass for set_config: only hass.config.path is touched."""

    def __init__(self):
        self.config = types.SimpleNamespace(path=lambda *_a: tempfile.mkdtemp())


class TokenSignalTests(unittest.TestCase):
    def tearDown(self):
        REE.last_error = None

    def test_missing_token_sets_no_token_signal(self):
        EnchufadoCoordinator.set_config(_config(""), _FakeHass())
        self.assertEqual(REE.last_error, "no_token")

    def test_present_token_clears_signal(self):
        REE.last_error = "no_token"
        EnchufadoCoordinator.set_config(_config("tok"), _FakeHass())
        self.assertIsNone(REE.last_error)

    def test_repair_mapping_covers_no_token(self):
        from custom_components.enchufado.repairs import _REE_KEYS

        self.assertEqual(_REE_KEYS["no_token"], "esios_token_missing")


# ---------------------------------------------------------------------------
# MAJOR-5: statistics rebuild off the event loop
# ---------------------------------------------------------------------------
def _sample_data(hours=60 * 24):
    start = datetime.datetime(2024, 3, 1, 0)
    consumptions, prices = {}, {}
    for i in range(hours):
        ts = madrid_timestamp(start + datetime.timedelta(hours=i))
        consumptions[ts] = {"value": 0.25, "reading_type": "R"}
        prices[ts] = 0.15
    return consumptions, prices


class _RecordingHass:
    def __init__(self):
        self.executor_jobs = []
        self.external_stats = []

    async def async_add_executor_job(self, fn, *args):
        self.executor_jobs.append(fn)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(fn, *args).result()


class OffLoopTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        Datadis.last_error = None

    async def test_reprocess_runs_create_statistics_in_executor(self):
        consumptions, prices = _sample_data(24)

        async def fake_load(hass, file_path, start_date=None):
            return consumptions, prices

        orig_load = EnchufadoCoordinator.__dict__["load_energy_data"]
        orig_add = coord_mod.async_add_external_statistics

        def fake_add(hass, metadata, stats):
            hass.external_stats.append((metadata, stats))

        EnchufadoCoordinator.load_energy_data = staticmethod(fake_load)
        coord_mod.async_add_external_statistics = fake_add
        try:
            hass = _RecordingHass()
            await EnchufadoCoordinator.reprocess_energy_data(hass)
            self.assertIn(EnchufadoCoordinator.create_statistics, hass.executor_jobs)
            self.assertEqual(len(hass.external_stats), 2)
        finally:
            EnchufadoCoordinator.load_energy_data = orig_load
            coord_mod.async_add_external_statistics = orig_add

    def test_create_statistics_is_thread_safe_pure_function(self):
        consumptions, prices = _sample_data(24)
        direct = EnchufadoCoordinator.create_statistics(0, consumptions, prices, 0.0, 0.0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            threaded = ex.submit(
                EnchufadoCoordinator.create_statistics, 0, consumptions, prices, 0.0, 0.0
            ).result()
        self.assertEqual(len(direct[0]), len(threaded[0]))
        self.assertEqual(len(direct[1]), len(threaded[1]))
        self.assertEqual([s.sum for s in direct[0]], [s.sum for s in threaded[0]])
        self.assertEqual([s.sum for s in direct[1]], [s.sum for s in threaded[1]])


if __name__ == "__main__":
    unittest.main()
