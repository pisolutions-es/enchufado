"""MAJOR-5 regression tests: the statistics rebuild must not run on the loop.

create_statistics() iterates one row per hour of history (~17k iterations
for 2 years, two datetime.fromtimestamp calls each). It touches no async HA
APIs, so it must run via hass.async_add_executor_job instead of stalling
the event loop for 100-400 ms per rebuild.
"""
import concurrent.futures
import datetime

import pytest

from custom_components.enchufado import coordinator as coord_mod
from custom_components.enchufado.coordinator import EnchufadoCoordinator
from custom_components.enchufado.util import madrid_timestamp

pytestmark = pytest.mark.timeout(60)


class RecordingHass:
    """Fake hass: runs executor jobs in a real thread and records them."""

    def __init__(self):
        self.executor_jobs = []
        self.external_stats = []

    async def async_add_executor_job(self, fn, *args):
        self.executor_jobs.append(fn)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(fn, *args).result()


def _two_years_of_hourly_data():
    start = datetime.datetime(2024, 3, 1, 0)
    consumptions = {}
    prices = {}
    for i in range(24 * 60):  # 60 days keeps the test fast; shape is identical
        ts = madrid_timestamp(start + datetime.timedelta(hours=i))
        consumptions[ts] = {"value": 0.25, "reading_type": "R"}
        prices[ts] = 0.15
    return consumptions, prices


async def test_reprocess_runs_create_statistics_in_executor(monkeypatch):
    consumptions, prices = _two_years_of_hourly_data()

    async def fake_load(hass, file_path, start_date=None):
        return consumptions, prices

    monkeypatch.setattr(EnchufadoCoordinator, "load_energy_data", fake_load)
    hass = RecordingHass()

    await EnchufadoCoordinator.reprocess_energy_data(hass)

    assert EnchufadoCoordinator.create_statistics in hass.executor_jobs
    assert len(hass.external_stats) == 2  # consumption + cost statistics added


def test_create_statistics_is_thread_safe_pure_function():
    """It must produce identical results on and off the event loop's thread."""
    consumptions, prices = _two_years_of_hourly_data()

    direct = EnchufadoCoordinator.create_statistics(0, consumptions, prices, 0.0, 0.0)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        threaded = ex.submit(
            EnchufadoCoordinator.create_statistics, 0, consumptions, prices, 0.0, 0.0
        ).result()

    assert len(direct[0]) == len(threaded[0]) == len(direct[1]) == len(threaded[1])
    assert [s.sum for s in direct[0]] == [s.sum for s in threaded[0]]
    assert [s.sum for s in direct[1]] == [s.sum for s in threaded[1]]


async def test_import_full_rebuild_runs_in_executor(monkeypatch):
    """The full-history branch of import_energy_data uses the executor too."""
    consumptions, prices = _two_years_of_hourly_data()

    async def fake_load(hass, file_path, start_date=None):
        return consumptions, prices

    async def fake_get_data(*args, **kwargs):
        return None

    async def fake_save(hass, file_path, consumptions, prices):
        return None

    async def fake_periods(hass, consumptions):
        return []

    async def fake_bills(hass, periods, consumptions, force_update=False):
        return None

    async def fake_repairs(hass):
        return None

    def fake_get_last_statistics(*args, **kwargs):
        return {}

    monkeypatch.setattr(EnchufadoCoordinator, "load_energy_data", fake_load)
    monkeypatch.setattr(EnchufadoCoordinator, "get_data", fake_get_data)
    monkeypatch.setattr(EnchufadoCoordinator, "save_energy_data", fake_save)
    monkeypatch.setattr(EnchufadoCoordinator, "get_billing_periods", fake_periods)
    monkeypatch.setattr(EnchufadoCoordinator, "calculate_bills", fake_bills)
    monkeypatch.setattr(coord_mod, "async_manage_repairs", fake_repairs)
    monkeypatch.setattr(
        coord_mod, "get_last_statistics", fake_get_last_statistics
    )
    monkeypatch.setattr(coord_mod, "get_instance", lambda hass: hass)

    hass = RecordingHass()
    await EnchufadoCoordinator.import_energy_data(hass, force_update=True)

    assert EnchufadoCoordinator.create_statistics in hass.executor_jobs
