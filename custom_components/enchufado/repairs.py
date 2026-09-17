"""Repair issues surfaced in the HA 'Repairs' UI.

Issues are created/cleared at the end of every import cycle based on the
last-error health signals of the upstream clients (Datadis, REE). They are
informational (no repair flow yet — see odd/findings.md #1 for the planned
reauth flow), but they take the outage out of the debug log and into the UI.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .datadis import Datadis
from .ree import REE

_LOGGER = logging.getLogger(__name__)

_ISSUE_DATADIS = f"{DOMAIN}_datadis_unavailable"
_ISSUE_REE = f"{DOMAIN}_esios_unavailable"

# one translation_key per failure reason (UI-localizable, no raw placeholders)
_DATADIS_KEYS = {
    "auth": "datadis_auth_failed",
    "quota": "datadis_quota_exhausted",
    "network": "datadis_unreachable",
}
_REE_KEYS = {
    "auth": "esios_token_rejected",
    "no_token": "esios_token_missing",
    "network": "esios_unreachable",
}


def _manage(hass: HomeAssistant, key: str, translation_key: str | None,
            severity) -> None:
    if translation_key is None:
        if ir.async_get(hass).async_get_issue(DOMAIN, key) is not None:
            ir.async_delete_issue(hass, DOMAIN, key)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        key,
        is_fixable=False,
        severity=severity,
        translation_key=translation_key,
    )


async def async_manage_repairs(hass: HomeAssistant) -> None:
    """Create or clear repair issues matching the clients' health signals."""
    try:
        _manage(
            hass,
            _ISSUE_DATADIS,
            _DATADIS_KEYS.get(Datadis.last_error) if Datadis.last_error else None,
            ir.IssueSeverity.ERROR if Datadis.last_error == "auth" else ir.IssueSeverity.WARNING,
        )
        _manage(
            hass,
            _ISSUE_REE,
            _REE_KEYS.get(REE.last_error) if REE.last_error else None,
            ir.IssueSeverity.ERROR if REE.last_error in ("auth", "no_token") else ir.IssueSeverity.WARNING,
        )
    except Exception:  # repairs must never break the import cycle
        _LOGGER.exception("Failed to update Enchufado repair issues")


async def async_clear_repairs(hass: HomeAssistant) -> None:
    """Remove our issues on unload so stale ones don't outlive the integration."""
    for key in (_ISSUE_DATADIS, _ISSUE_REE):
        ir.async_delete_issue(hass, DOMAIN, key)
