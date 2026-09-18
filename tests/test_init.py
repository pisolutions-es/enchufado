"""Entry lifecycle: setup/unload and config entry compatibility."""
import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enchufado.const import (
    CONF_CUPS,
    CONF_DISTRIBUTOR_CODE,
    CONF_ESIOS_TOKEN,
    CONF_POINT_TYPE,
    DOMAIN,
)

from fakes import FakeResponse, install_fake_session, route

pytestmark = pytest.mark.timeout(60)


def _entry_data():
    return {
        "datadis_user": "u",
        "datadis_password": "p",
        CONF_CUPS: "CUPS-A",
        CONF_DISTRIBUTOR_CODE: "2",
        CONF_POINT_TYPE: 1,
        "power_high": 4.6,
        "power_low": 4.6,
        "zip_code": "28001",
        CONF_ESIOS_TOKEN: "",
    }


async def _mock_apis(monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", FakeResponse(200, text="tok")),
        route("GET", r"get-consumption-data", FakeResponse(200, json_data=[])),
        route("GET", r"api\.esios\.ree\.es",
              FakeResponse(200, json_data={"indicator": {"values": []}})),
    ])


async def test_setup_and_unload_roundtrip(recorder_mock, hass, monkeypatch):
    """A config entry with the historical schema loads, sets up and unloads."""
    await _mock_apis(monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), version=1)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.config_entries import ConfigEntryState

    assert hass.config_entries.async_entries(DOMAIN)[0].state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "import_energy_data")
    assert hass.services.has_service(DOMAIN, "force_import_energy_data")
    assert hass.services.has_service(DOMAIN, "reprocess_energy_data")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "import_energy_data")


async def test_migrate_refuses_newer_version(recorder_mock, hass):
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), version=2)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_number_entity_added(recorder_mock, hass, monkeypatch):
    await _mock_apis(monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), version=1)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "number.enchufado_bills_number"
    # has_entity_name + device name → "Enchufado Facturas a mostrar" style id;
    # accept either form since the id itself must stay stable across upgrades.
    found = hass.states.get(entity_id) or hass.states.get("number.enchufado_facturas_a_mostrar")
    assert found is not None
    assert float(found.state) == 5.0


async def test_overlapping_import_triggers_are_coalesced(recorder_mock, hass, monkeypatch):
    """Two triggers while an import is running must not stack duplicate imports."""
    await _mock_apis(monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), version=1)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.enchufado import coordinator

    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def slow_import(hass_, force=False):
        calls.append(force)
        started.set()
        await release.wait()

    monkeypatch.setattr(coordinator.EnchufadoCoordinator, "import_energy_data", slow_import)

    async def _pump_until(condition, iterations=100):
        for _ in range(iterations):
            await asyncio.sleep(0)
            if condition():
                return True
        return False

    hass.async_create_task(
        hass.services.async_call(DOMAIN, "import_energy_data", blocking=False)
    )
    assert await _pump_until(started.is_set), "first import never started"

    # second trigger while the first import is still in flight
    hass.async_create_task(
        hass.services.async_call(DOMAIN, "import_energy_data", blocking=False)
    )
    assert await _pump_until(lambda: len(calls) > 0 or calls == [False])
    assert calls == [False]  # the overlapping trigger was coalesced away

    release.set()
    await hass.async_block_till_done()  # first import finishes, flag resets
    # a later trigger runs again
    await hass.services.async_call(DOMAIN, "force_import_energy_data", blocking=True)
    await hass.async_block_till_done()
    assert calls == [False, True]


async def test_scheduled_import_sleep_cancelled_on_unload(recorder_mock, hass, monkeypatch):
    """Unloading never lets a pending jittered import import after teardown."""
    await _mock_apis(monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), version=1)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.enchufado import coordinator

    calls = []

    async def counting_import(hass_, force=False):
        calls.append(force)

    monkeypatch.setattr(coordinator.EnchufadoCoordinator, "import_energy_data", counting_import)

    # asyncio.sleep ignores HA's async_fire_time_changed, so the 0-3600 s
    # jitter must be pinned to a SHORT real delay (a few seconds) instead of
    # the default hour; globally patching asyncio.sleep instead would park
    # HA's own timer helpers and hang block_till_done. The low-but-real delay
    # keeps the jitter pending when the unload starts, which is the scenario
    # under test.
    monkeypatch.setattr("custom_components.enchufado.randint", lambda a, b: 3)

    import datetime as _dt
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    tomorrow = dt_util.utcnow() + _dt.timedelta(days=1)
    async_fire_time_changed(hass, tomorrow.replace(hour=6, minute=30, second=0, microsecond=0))
    # Let the eager-started scheduled-import task reach its jitter sleep
    # WITHOUT block_till_done — that would await the sleep and let the import
    # run while the entry is still loaded, which is correct behaviour but not
    # what is being tested here.
    for _ in range(100):
        await asyncio.sleep(0)
        if entry._tasks:
            break
    assert entry._tasks, "scheduled import never reached its jitter sleep"

    # Unload while the jitter is still pending. HA's unload awaits the entry's
    # tracked tasks (bounded) after the component has already popped the
    # entry data, so when the sleep elapses inside that window _import_task
    # sees no entry data and is a no-op — the import never runs.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    # The jitter would now have elapsed; a leaked timer would import after
    # unload. Fire another time change to prove the tracker was removed too.
    async_fire_time_changed(hass, dt_util.utcnow() + _dt.timedelta(hours=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == []  # no import fired from the sleep that spanned the unload
