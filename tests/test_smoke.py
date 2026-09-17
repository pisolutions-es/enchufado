"""Smoke tests: harness wiring."""
from homeassistant.const import __version__ as HA_VERSION


async def test_smoke(hass):
    assert hass is not None
    assert int(HA_VERSION.split(".")[0]) >= 2025


def test_modules_import():
    import custom_components.enchufado.config_flow  # noqa: F401
    import custom_components.enchufado.coordinator  # noqa: F401
    import custom_components.enchufado.cnmc  # noqa: F401
    import custom_components.enchufado.datadis  # noqa: F401
    import custom_components.enchufado.ree  # noqa: F401
