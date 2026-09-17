"""Enchufado — PVPC energy statistics via Datadis."""
import asyncio
import logging
from os import makedirs
from os.path import exists
from random import randint

from homeassistant.const import Platform
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN
from .coordinator import EnchufadoCoordinator
from .datadis import close_session as close_datadis_session

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.NUMBER]


async def async_setup_entry(hass, entry) -> bool:
    _LOGGER.debug("async_setup_entry: entry_id=%s", entry.entry_id)
    hass_data = dict(entry.data)
    EnchufadoCoordinator.set_config(hass_data, hass)

    await hass.async_add_executor_job(_ensure_data_dir)

    unsub = entry.add_update_listener(options_update_listener)
    hass_data["unsub_options_update_listener"] = unsub
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hass_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _setup_services(hass, entry)
    _import_task(hass, entry)
    return True


def _ensure_data_dir():
    if not exists(EnchufadoCoordinator.user_files_path):
        makedirs(EnchufadoCoordinator.user_files_path)


def _import_task(hass, entry, force: bool = False) -> None:
    """Start an import unless one is already running for this entry.

    The flag lives in entry data so concurrent triggers (scheduled, service,
    setup) no longer stack duplicate import chains that double API calls and
    race on the CSV/statistics writes. The reset runs in a done-callback so a
    raised import error cannot leave the flag stuck.
    """
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data is None:
        return
    if data.get("import_running"):
        _LOGGER.debug("import already running, skipping overlapping trigger (force=%s)", force)
        return
    data["import_running"] = True

    async def _run() -> None:
        await EnchufadoCoordinator.import_energy_data(hass, force)

    task = entry.async_create_task(hass, _run(), "enchufado import")
    task.add_done_callback(lambda _: data.pop("import_running", None))


def _setup_services(hass, entry) -> None:
    async def _handle_import(call):
        _import_task(hass, entry)

    async def _handle_force_import(call):
        _import_task(hass, entry, force=True)

    async def _handle_reprocess(call):
        entry.async_create_task(hass, EnchufadoCoordinator.reprocess_energy_data(hass), "enchufado reprocess")

    async def _handle_scheduled_import(now):
        # The sleep is a task tracked by the entry so an unload/reload before
        # the jitter elapses cancels it instead of leaking a timer + import.
        entry.async_create_task(hass, _delayed_import(), "enchufado scheduled import")

    async def _delayed_import() -> None:
        await asyncio.sleep(randint(0, 3600))
        _import_task(hass, entry)

    hass.services.async_register(DOMAIN, "import_energy_data", _handle_import)
    hass.services.async_register(DOMAIN, "force_import_energy_data", _handle_force_import)
    hass.services.async_register(DOMAIN, "reprocess_energy_data", _handle_reprocess)
    unsub_tracker = async_track_time_change(hass, _handle_scheduled_import, hour=6, minute=30, second=0)
    entry.async_on_unload(unsub_tracker)


async def options_update_listener(hass, config_entry):
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_migrate_entry(hass, config_entry) -> bool:
    """Migrate a config entry to the current schema version.

    The data schema has been stable since the first release (version 1), so
    there is nothing to rewrite; entries from older fork builds load as-is.
    Entries from a NEWER version (downgrade) are refused rather than loaded
    half-migrated.
    """
    if config_entry.version > 1:
        _LOGGER.error(
            "Enchufado config entry version %s is newer than this integration "
            "supports — update the Enchufado integration",
            config_entry.version,
        )
        return False
    return True


async def async_unload_entry(hass, entry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        entry_data["unsub_options_update_listener"]()
        await close_datadis_session()
        hass.services.async_remove(DOMAIN, "import_energy_data")
        hass.services.async_remove(DOMAIN, "force_import_energy_data")
        hass.services.async_remove(DOMAIN, "reprocess_energy_data")
    return unloaded


