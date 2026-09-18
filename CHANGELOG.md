# Changelog

All notable changes to this fork are documented here. This fork is based on
[Migux13/enchufado](https://github.com/Migux13/enchufado) (MIT); the v1.x
releases build on this fork's own [1.0.0-fork](#100-fork--2026-09-17).

## [1.1.0-fork] — 2026-09-18

Hardening release from the v1.1.0 audit: calendar correctness, import
lifecycle, response validation and user-visible API health. No entity IDs,
statistic IDs or config-entry data were changed: existing installations
upgrade in place with no user action.

### Added
- **HA Repairs UI integration**: upstream API failures are no longer silent
  debug-log noise. Datadis errors (auth rejected, daily quota exhausted,
  unreachable) and REE/ESIOS errors (token rejected, missing, unreachable)
  now surface as repair issues under Settings → Repairs, with Spanish and
  English strings. Issues auto-clear when the client recovers on the next
  import cycle and are cleaned up on integration unload, so no stale entries
  linger after a reload.
- Test suite grew to 72 tests (from 49): response-validation tests for all
  three clients, Madrid-calendar tests, repair-issue lifecycle tests and
  import-coalescing / cancellation tests.

### Fixed
- **Timezone-correct calendar dates**: `date.today()` resolves in the *host*
  timezone, so on UTC-configured installs (Docker/NAS/cloud) the daily
  import window and the current-month billing boundary shifted by a day for
  the last hours of each Madrid day. All calendar logic now uses an explicit
  `madrid_today()` (Europe/Madrid).
- **Overlapping import triggers are coalesced**: a second trigger (service
  call, scheduled run, setup) while an import is already running no longer
  stacks a duplicate import chain that doubled Datadis/REE API calls and
  raced on the CSV/statistics writes. The in-flight guard resets in a
  done-callback, so a failed import cannot wedge it.
- **No leaked scheduled imports**: the 0–3600 s jitter sleep of the 06:30
  scheduled import is now a task tracked by the config entry, so an
  unload/reload before the jitter elapses cancels it instead of leaking a
  sleeping task that would later import against a torn-down hass.
- **Clients validate responses instead of trusting them**:
  - Datadis login: an HTML/empty 200 body is no longer stored as the auth
    token, and a 200 with a non-JSON body (WAF page) fails fast instead of
    burning three retries of the daily quota.
  - Datadis supplies/contract/timeCurve: dict-instead-of-list payloads,
    non-dict items and NaN/bool/negative kWh records are skipped with a log
    instead of raising deep inside the import task.
  - REE/ESIOS: a missing `indicator.values` or a non-JSON 200 is now a hard
    failure that stops the chunk loop; malformed per-value entries are
    skipped and NaN prices dropped.
  - CNMC: the bill GET status is checked, and non-dict/JSON payloads or
    incomplete `gasto` blocks leave the period unpriced instead of raising
    `KeyError`.

### Notes for users of the fork
- If Datadis or REE goes down, look for an Enchufado card in Settings →
  Repairs; it disappears automatically once the API recovers.
- Behaviour of service calls (`import_energy_data`,
  `force_import_energy_data`, `reprocess_energy_data`), entities, statistics
  and stored CSV files is unchanged from 1.0.0-fork.

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

[1.1.0-fork]: https://github.com/pisolutions-es/enchufado/releases/tag/v1.1.0-fork
[1.0.0-fork]: https://github.com/pisolutions-es/enchufado/releases/tag/v1.0.0-fork
