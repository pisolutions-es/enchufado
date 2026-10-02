# Changelog

All notable changes to this fork are documented here. This fork is based on
[Migux13/enchufado](https://github.com/Migux13/enchufado) (MIT); the v1.x
releases build on this fork's own [1.0.0-fork](#100-fork--2026-09-17).

## [1.2.0-fork] — 2026-10-02

Second fix pass from the REVIEW-CRITICAL-2026-10 audit (the CRITICAL-1 and
MAJOR-1/3/5 findings were fixed in 1.1.1-fork). No entity IDs, statistic IDs
or config-entry data were changed: existing installations upgrade in place
with no user action.

### Added
- **Options flow for credentials**: the `datadis_auth_failed` repair issue
  now points at a flow that actually exists. Go to Settings → Devices &
  Services → Enchufado → ⚙️ Configure to re-enter the Datadis
  username/password (validated against the API before saving), the
  authorized NIF or the ESIOS-REE token — no need to delete and re-add the
  integration. Blank fields keep the stored value; saving reloads the entry
  automatically. `esios_token_rejected` and `esios_token_missing` copy
  updated accordingly.
- **Complete English translations** (`translations/en.json`): the setup
  wizard, the options flow and every repair issue are now available in
  English. `strings.json` is now the canonical English source (it
  previously mixed a Spanish wizard with English repair copy); `es.json`
  carries the full Spanish set.

### Changed
- **REE and CNMC reuse one shared aiohttp session** instead of opening a
  short-lived `ClientSession` per request (a force import used to open ~26
  REE sessions plus up to 5 CNMC ones). Timeouts are unchanged and the
  sessions are closed on integration unload.
- `manifest.json` now points `documentation`, `issue_tracker` and
  `codeowners` at the pisolutions-es fork (upstream remains credited in
  `FORK-NOTE.md`, `NOTICE` and the source headers).

### Fixed
- **Statistics-resume hardening (MAJOR-2)**: the incremental-resume fast
  path no longer raises when the recorder returns an empty
  `get_last_statistics` payload or no rows from
  `statistics_during_period` — it falls back to the full-history rebuild
  instead of crashing the import cycle. The branch is now covered by
  behavior tests: resume from the recorder's last point, the float-epoch
  `start` contract, both nothing-to-resume fallbacks and the
  UTC/Madrid date-mismatch (day-boundary) rebuild.
- **README accuracy**: the number entity is
  `number.enchufado_facturas_a_mostrar` (the documented
  `number.facturas_a_mostrar` does not exist); the daily schedule is
  documented as 6:30 plus a 0–60 min jitter; and the privacy note spells
  out that the CNMC simulation uploads the full hourly consumption curve
  (including the CUPS) to comparador.cnmc.gob.es.

### Notes for users of the fork
- Existing entries need no action. To change credentials from now on, use
  the gear icon on the Enchufado card instead of deleting the integration.
- The offline test suite (stdlib `unittest`, no homeassistant needed)
  grew to 30 tests: `python3 -m unittest tests.test_pass2_offline -v`.
  The repo-style pytest suite requires pytest-homeassistant-custom-component
  and runs on CI hosts only.

## [1.1.1-fork] — 2026-10-02

Robustness release from the critical code review of 2026-10-02
(`REVIEW-CRITICAL-2026-10.md`): fixes the one availability bug that could
silently stop all data collection, and the data-loss path that could destroy
two years of cached history. No entity IDs, statistic IDs or config-entry
data were changed: existing installations upgrade in place with no user
action.

### Fixed
- **Unbounded `Retry-After` retry loop (CRITICAL-1)**: a Datadis 429/5xx that
  kept carrying a `Retry-After` header made the client retry forever (each
  sleep up to 1 hour), permanently wedging the import task — the
  `import_running` guard never cleared, so every later trigger (daily 06:30
  schedule, service calls, setup) was silently skipped until an HA restart.
  The `Retry-After` branch is now capped by the same attempt budget as the
  exponential-backoff branch; after `_MAX_RETRIES` attempts the request
  returns its last status, the import cycle completes, the guard is released
  and the failure surfaces as a repair issue (`datadis_quota_exhausted` for
  persistent 429s, `datadis_unreachable` for 5xx).
- **Non-atomic `energy_data.csv` writes (MAJOR-1, part 1)**: the file was
  truncated in place before writing, so a crash/power loss/OOM kill mid-write
  destroyed the entire ~2-year local history (not fully re-fetchable: Datadis
  caps retrieval at 2 years and CNMC refuses old files). Writes now go to
  `energy_data.csv.tmp`, are fsynced, and are moved into place with
  `os.replace()`.
- **Zero parse-error tolerance in `energy_data.csv` (MAJOR-1, part 2)**: a
  truncated final line or any corrupted row raised out of the import task and
  repeated on every cycle, silently importing nothing. Corrupted lines are now
  skipped with a warning (including a per-file summary count) and the import
  continues with the remaining history.
- **`esios_token_missing` repair issue was unreachable (MAJOR-3)**: nothing
  ever set `REE.last_error = "no_token"`, so the shipped `esios_token_missing`
  issue could never appear in the Repairs UI (the 1.1.0-fork CHANGELOG claim
  about "missing" ESIOS tokens becoming repair issues only becomes true with
  this release). `set_config()` now sets the signal when no ESIOS token is
  configured and clears it when one exists.
- **Statistics rebuild stalled the event loop (MAJOR-5)**: the ~17,000-
  iteration (2 years hourly) `create_statistics()` loop ran synchronously on
  the event loop, stalling it ~100–400 ms per rebuild. It touches no async HA
  APIs, so all three call sites (full rebuild and incremental resume in the
  import cycle, plus the reprocess service) now run it via
  `hass.async_add_executor_job`.

### Added
- Regression tests for the four fixes: bounded retries with `Retry-After`
  (request + login, 429 and 5xx), atomic-write crash simulation, corrupted/
  truncated/blank-line CSV parsing, the `no_token` repair wiring, and
  executor-routing of the statistics rebuild
  (`tests/test_retry_bounded.py`, `tests/test_csv_robustness.py`,
  `tests/test_esios_token_missing.py`, `tests/test_statistics_off_loop.py`,
  plus `tests/test_fixes_offline.py`, a stdlib-unittest variant runnable
  without homeassistant installed).

[1.2.0-fork]: https://github.com/pisolutions-es/enchufado/releases/tag/v1.2.0-fork
[1.1.1-fork]: https://github.com/pisolutions-es/enchufado/releases/tag/v1.1.1-fork
[1.1.0-fork]: https://github.com/pisolutions-es/enchufado/releases/tag/v1.1.0-fork
[1.0.0-fork]: https://github.com/pisolutions-es/enchufado/releases/tag/v1.0.0-fork

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
