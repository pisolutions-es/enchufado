"""Repair-issue lifecycle (audit T5): issues appear and clear with client health."""
import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enchufado import repairs
from custom_components.enchufado.const import DOMAIN
from custom_components.enchufado.datadis import Datadis
from custom_components.enchufado.ree import REE

pytestmark = pytest.mark.timeout(60)


def _issue_ids(hass):
    return {issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN}


async def test_datadis_error_creates_issue_and_recovery_clears(hass):
    Datadis.last_error = "network"
    REE.last_error = None
    await repairs.async_manage_repairs(hass)
    assert "enchufado_datadis_unavailable" in _issue_ids(hass)

    Datadis.last_error = "quota"
    await repairs.async_manage_repairs(hass)  # updated in place, no duplicate
    assert len(_issue_ids(hass)) == 1

    Datadis.last_error = None
    await repairs.async_manage_repairs(hass)
    assert _issue_ids(hass) == set()


async def test_ree_auth_error_maps_to_token_issue(hass):
    Datadis.last_error = None
    REE.last_error = "auth"
    await repairs.async_manage_repairs(hass)
    issues = ir.async_get(hass)
    issue = issues.async_get_issue(DOMAIN, "enchufado_esios_unavailable")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "esios_token_rejected"

    REE.last_error = None
    await repairs.async_manage_repairs(hass)
    assert issues.async_get_issue(DOMAIN, "enchufado_esios_unavailable") is None


async def test_clear_repairs_on_unload_removes_stale_issues(hass):
    REE.last_error = "network"
    await repairs.async_manage_repairs(hass)
    assert _issue_ids(hass) == {"enchufado_esios_unavailable"}
    await repairs.async_clear_repairs(hass)
    assert _issue_ids(hass) == set()
    await repairs.async_clear_repairs(hass)  # idempotent


async def test_manage_repairs_never_raises(hass, monkeypatch):
    """A broken issue registry must not take down the import cycle."""
    monkeypatch.setattr(repairs, "_manage", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    await repairs.async_manage_repairs(hass)  # swallowed
