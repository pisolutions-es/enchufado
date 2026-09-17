# Changelog

All notable changes to this fork are documented here. This fork is based on
[Migux13/enchufado](https://github.com/Migux13/enchufado) (MIT) and this
release is compared against upstream **v0.3.1**.

## [1.0.0-fork] — 2026-09-17

Quality and resilience release for the pisolutions-es fork. No entity IDs,
statistic IDs or config-entry data were changed: existing installations
upgrade in place with no user action.

### Added
- Test suite (`tests/`, 49 tests, pytest + pytest-homeassistant-custom-component)
  covering the coordinator (energy CSV persistence incl. legacy format, hourly
  statistics, billing-period CSV, monthly period generation), the Datadis
  client (token reuse, 401 refresh, curve parsing, malformed records),
  the REE price client, the CNMC bill simulator (upload/bill flow, warning
  paths, data-range guards), the config flow (full two-step flow, error
  forms, contract fallback) and the entry lifecycle (setup/unload roundtrip,
  migration guard, number entity).
- `async_migrate_entry` with an explicit `ConfigFlow.VERSION = 1`; config
  entries from a newer version (downgrade scenario) are now refused with a
  clear error instead of loading half-migrated.
- `CHANGELOG.md`.

### Fixed / improved
- **Datadis client resilience**: shared `aiohttp` session with explicit
  timeouts (60 s total / 15 s connect) instead of one un-timed session per
  request; bounded exponential backoff with jitter on connection errors,
  429 and 5xx; `Retry-After` honored (clamped to 1 s–1 h); a repeated 429
  without `Retry-After` is treated as the daily quota being exhausted and
  short-circuits further requests until next midnight (peninsular Spain)
  instead of burning the quota with retries. The shared session is closed
  on entry unload.
- **Timeouts everywhere**: REE/ESIOS and CNMC requests now have explicit
  client timeouts; a REE non-200 response is logged instead of failing
  silently.
- **No write amplification**: `enchufado.current_bill` is only rewritten
  when its state or attributes actually change, so idle cycles no longer
  add recorder event rows. Regression tests pin the caching contract: an
  up-to-date import cycle makes zero Datadis calls, never rewrites
  `energy_data.csv`, and re-inserts no consumption/cost statistics; chunked
  price fetching only requests the missing tail after the cached horizon.
- **Config flow isolation**: supplies list, login token and form data moved
  from class attributes to instance attributes so concurrent flows cannot
  leak each other's state.
- **CancelledError** is re-raised in client retry loops so HA reloads/shutdown
  are never swallowed.
- Coordinator type hints and docstrings; dead imports removed; class-level
  attribute defaults cleaned up (no functional change).

### Notes for users of the fork
- Behaviour of service calls (`import_energy_data`,
  `force_import_energy_data`, `reprocess_energy_data`), entities, statistics
  and stored CSV files is unchanged.
- If Datadis keeps answering "too many requests", the integration now waits
  until the quota resets instead of hammering the API; check the logs for the
  "daily quota" warning.

[1.0.0-fork]: https://github.com/pisolutions-es/enchufado/releases/tag/v1.0.0-fork
