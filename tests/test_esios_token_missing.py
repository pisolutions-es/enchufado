"""MAJOR-3 regression test: esios_token_missing repair issue is reachable.

Before the fix nothing ever set REE.last_error = "no_token", so the
esios_token_missing repair issue (strings, severity, mapping all shipped)
was dead code and the CHANGELOG claim about "missing" ESIOS token errors
surfacing as repair issues was false.
"""
import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.enchufado import repairs
from custom_components.enchufado.const import DOMAIN
from custom_components.enchufado.coordinator import EnchufadoCoordinator
from custom_components.enchufado.ree import REE

pytestmark = pytest.mark.timeout(60)


def _config(token):
    return {
        "datadis_user": "u",
        "datadis_password": "p",
        "cups": "CUPS",
        "distributor_code": "2",
        "point_type": 1,
        "esios_token": token,
    }


class _FakeConfig:
    def path(self, *_args):
        return "/tmp/enchufado-test"


async def test_missing_token_sets_no_token_signal():
    EnchufadoCoordinator.set_config(_config(""), _FakeConfig())
    assert REE.last_error == "no_token"

    from custom_components.enchufado.repairs import _REE_KEYS

    assert _REE_KEYS["no_token"] == "esios_token_missing"


async def test_present_token_clears_no_token_signal():
    REE.last_error = "no_token"
    EnchufadoCoordinator.set_config(_config("tok"), _FakeConfig())
    assert REE.last_error is None


async def test_missing_token_surfaces_esios_token_missing_issue(hass):
    """End to end: set_config wires the signal, the repairs pass raises the
    issue with the shipped translation key."""
    REE.last_error = None
    EnchufadoCoordinator.set_config(_config(""), _FakeConfig())
    await repairs.async_manage_repairs(hass)

    issues = ir.async_get(hass)
    issue = issues.async_get_issue(DOMAIN, "enchufado_esios_unavailable")
    assert issue is not None
    assert issue.translation_key == "esios_token_missing"
    assert issue.severity is ir.IssueSeverity.ERROR

    # Adding a token (reload → set_config) clears it on the next cycle.
    EnchufadoCoordinator.set_config(_config("tok"), _FakeConfig())
    await repairs.async_manage_repairs(hass)
    assert issues.async_get_issue(DOMAIN, "enchufado_esios_unavailable") is None
