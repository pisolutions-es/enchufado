"""Shared fixtures for Enchufado tests.

The integration lives under custom_components (not homeassistant/components),
so modules and the config flow are exercised directly with mocked HTTP.
The pytest-homeassistant-custom-component plugin auto-registers its fixtures
(hass, recorder_mock, enable_custom_integrations, ...).
"""
from pathlib import Path

import pytest

import custom_components

# pytest-homeassistant-custom-component ships its own regular
# custom_components package that shadows the repo's namespace package;
# extend its __path__ so `custom_components.enchufado` resolves here.
_REPO_CC = str(Path(__file__).resolve().parent.parent / "custom_components")
if _REPO_CC not in list(custom_components.__path__):
    custom_components.__path__.append(_REPO_CC)


@pytest.fixture(autouse=True)
def auto_custom_integration_setup(request):
    """Wire custom integrations, respecting the plugin's recorder bootstrap order.

    Tests that touch config entries must list `recorder_mock` in their
    signature; this fixture forces it to resolve BEFORE `hass` (the plugin
    asserts hass is not set up when it prepares the recorder database), then
    drops the custom-components scan the recorder setup cached.
    """
    if "recorder_mock" in request.fixturenames:
        from homeassistant import loader

        request.getfixturevalue("recorder_mock")
        hass = request.getfixturevalue("hass")
        hass.data.pop(loader.DATA_CUSTOM_COMPONENTS, None)
    else:
        request.getfixturevalue("enable_custom_integrations")
    yield
