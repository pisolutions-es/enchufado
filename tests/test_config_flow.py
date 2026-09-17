"""Config flow tests with mocked Datadis HTTP."""
import pytest

from custom_components.enchufado.const import (
    CONF_CUPS,
    CONF_DISTRIBUTOR_CODE,
    CONF_ESIOS_TOKEN,
    CONF_POINT_TYPE,
    CONF_POWER_HIGH,
    CONF_POWER_LOW,
    CONF_ZIP_CODE,
    DOMAIN,
)
from custom_components.enchufado.datadis import Datadis

from fakes import FakeResponse, install_fake_session, route

SUPPLIES = {"supplies": [
    {"cups": "CUPS-A", "pointType": "1", "distributorCode": "2", "postalCode": "28001"},
    {"cups": "CUPS-B", "pointType": "1", "distributorCode": "3"},
]}
CONTRACT = {"contract": [{"startDate": "2024-01-01", "contractedPowerkW": [5.75, 3.3]}]}


@pytest.fixture
def mock_datadis(monkeypatch):
    """Login+supplies always OK; entry setup's background import gets empty data."""
    return lambda routes: install_fake_session(monkeypatch, [
        *routes,
        route("POST", r"nikola-auth", FakeResponse(200, text="tok")),
        route("GET", r"get-supplies", FakeResponse(200, json_data=SUPPLIES)),
        route("GET", r"get-consumption-data", FakeResponse(200, json_data=[])),
        route("GET", r"api\.esios\.ree\.es",
              FakeResponse(200, json_data={"indicator": {"values": []}})),
    ])


async def test_full_flow_creates_entry(recorder_mock, hass, mock_datadis):
    mock_datadis([route("GET", r"get-contract-detail", FakeResponse(200, json_data=CONTRACT))])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"},
        data={"datadis_user": "user", "datadis_password": "pw",
              "authorized_nif": "", CONF_ESIOS_TOKEN: "reetok"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "cups"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_CUPS: "CUPS-A (E-distribución)"},
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "CUPS-A"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data[CONF_CUPS] == "CUPS-A"
    assert entry.data[CONF_DISTRIBUTOR_CODE] == "2"
    assert entry.data[CONF_POINT_TYPE] == 1
    assert entry.data[CONF_POWER_HIGH] == 5.75
    assert entry.data[CONF_POWER_LOW] == 3.3
    assert entry.data[CONF_ZIP_CODE] == "28001"


async def test_login_failure_shows_cannot_connect(recorder_mock, hass, monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", FakeResponse(401, text="no")),
    ])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"},
        data={"datadis_user": "u", "datadis_password": "p",
              "authorized_nif": "", CONF_ESIOS_TOKEN: "t"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_no_supplies_shows_error(recorder_mock, hass, monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", FakeResponse(200, text="tok")),
        route("GET", r"get-supplies", FakeResponse(200, json_data={"supplies": []})),
    ])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"},
        data={"datadis_user": "u", "datadis_password": "p",
              "authorized_nif": "", CONF_ESIOS_TOKEN: "t"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "no_supplies"}


async def test_contract_fetch_failure_falls_back_to_defaults(recorder_mock, hass, mock_datadis):
    def boom(**_):
        raise OSError("contract endpoint down")

    mock_datadis([route("GET", r"get-contract-detail", boom)])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"},
        data={"datadis_user": "u", "datadis_password": "p",
              "authorized_nif": "", CONF_ESIOS_TOKEN: "t"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_CUPS: "CUPS-B (E-redes)"},
    )
    assert result["type"] == "create_entry"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data[CONF_POWER_HIGH] == 4.6
    assert entry.data[CONF_POWER_LOW] == 4.6
    assert entry.data[CONF_ZIP_CODE] == ""


async def test_two_flows_do_not_share_supplies_state(recorder_mock, hass, mock_datadis):
    """Class-attribute leakage guard: two concurrent flows must stay isolated."""
    mock_datadis([route("GET", r"get-contract-detail", FakeResponse(200, json_data=CONTRACT))])
    flow1 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"},
        data={"datadis_user": "u", "datadis_password": "p",
              "authorized_nif": "", CONF_ESIOS_TOKEN: "t"},
    )
    # Abandon flow1 at the cups step; start flow2 fresh.
    flow2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"},
        data={"datadis_user": "u", "datadis_password": "p",
              "authorized_nif": "", CONF_ESIOS_TOKEN: "t"},
    )
    assert flow2["step_id"] == "cups"
    # Both flows may still be in progress; configuring either must work.
    result = await hass.config_entries.flow.async_configure(
        flow2["flow_id"], user_input={CONF_CUPS: "CUPS-B (E-redes)"},
    )
    assert result["type"] == "create_entry"
