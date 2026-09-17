"""Unit tests for the Datadis client: parsing, login flow, error paths."""
import datetime

import pytest

from custom_components.enchufado import datadis
from custom_components.enchufado.datadis import Datadis, async_get_supplies
from custom_components.enchufado.util import madrid_timestamp

from fakes import FakeResponse, install_fake_session, route


@pytest.fixture(autouse=True)
def reset_datadis_state():
    Datadis.setup("u", "p", "cups", "2", 1)
    yield


def _setup_datadis():
    Datadis.setup("user", "pass", "ES-CUPS", "2", 1, authorized_nif=None)
    Datadis._token = "tok"


async def test_login_and_token_reuse(monkeypatch):
    Datadis.setup("user", "pass", "ES-CUPS", "2", 1)
    Datadis._token = None
    fake = install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth/tokens", FakeResponse(200, text="bearer-token")),
        route("GET", r"get-consumption-data", lambda **_: FakeResponse(200, json_data=[])),
    ])
    d = datetime.date(2026, 3, 1)
    await Datadis.consumptions(d, d)
    await Datadis.consumptions(d + datetime.timedelta(days=1), d + datetime.timedelta(days=1))
    posts = [c for c in fake["calls"] if c["method"] == "POST"]
    assert len(posts) == 1  # one login, token reused


async def test_consumptions_parsing_and_reading_type(monkeypatch):
    _setup_datadis()
    curve = [
        {"date": "2026/03/01", "time": "01:00", "consumptionKWh": "0.5", "obtainMethod": "Real"},
        {"date": "2026/03/01", "time": "02:00", "consumptionKWh": "0.25", "obtainMethod": "Estimate"},
        {"date": "2026/03/01", "time": "garbage", "consumptionKWh": "1"},
    ]
    install_fake_session(monkeypatch, [
        route("GET", r"get-consumption-data", FakeResponse(200, json_data={"timeCurve": curve})),
    ])
    d = datetime.date(2026, 3, 1)
    result = await Datadis.consumptions(d, d)
    t0 = madrid_timestamp(datetime.datetime(2026, 3, 1, 0))
    t1 = madrid_timestamp(datetime.datetime(2026, 3, 1, 1))
    assert result[t0] == {"value": 0.5, "reading_type": "R"}
    assert result[t1] == {"value": 0.25, "reading_type": "E"}
    assert len(result) == 2  # malformed record skipped, no exception


async def test_consumptions_out_of_range_filtered(monkeypatch):
    _setup_datadis()
    curve = [
        {"date": "2026/02/28", "time": "23:00", "consumptionKWh": "1", "obtainMethod": "Real"},
        {"date": "2026/03/01", "time": "01:00", "consumptionKWh": "1", "obtainMethod": "Real"},
    ]
    install_fake_session(monkeypatch, [
        route("GET", r"get-consumption-data", FakeResponse(200, json_data=curve)),
    ])
    d = datetime.date(2026, 3, 1)
    result = await Datadis.consumptions(d, d)
    assert len(result) == 1


async def test_consumptions_401_refreshes_token_once(monkeypatch):
    _setup_datadis()
    state = {"logins": 0, "gets": 0}

    def post(**_):
        state["logins"] += 1
        return FakeResponse(200, text=f"tok{state['logins']}")

    def get(**_):
        state["gets"] += 1
        return FakeResponse(401 if state["gets"] == 1 else 200, json_data=[])

    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", post),
        route("GET", r"get-consumption-data", get),
    ])
    d = datetime.date(2026, 3, 1)
    await Datadis.consumptions(d, d)
    assert state["logins"] == 1
    assert state["gets"] == 2


async def test_consumptions_network_error_returns_empty(monkeypatch):
    _setup_datadis()

    def boom(**_):
        raise OSError("connection reset")

    install_fake_session(monkeypatch, [route("GET", r"get-consumption-data", boom)])
    d = datetime.date(2026, 3, 1)
    assert await Datadis.consumptions(d, d) == {}


async def test_login_failure_returns_empty(monkeypatch):
    Datadis.setup("user", "pass", "ES-CUPS", "2", 1)
    Datadis._token = None
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", FakeResponse(401, text="bad creds")),
    ])
    d = datetime.date(2026, 3, 1)
    assert await Datadis.consumptions(d, d) == {}


async def test_get_supplies_normalizes(monkeypatch):
    payload = {"supplies": [
        {"cups": "A", "pointType": "1", "distributorCode": "2", "postalCode": "28001"},
        {"cups": "B", "pointType": 1, "distributorCode": 8},
        {"incomplete": True},
    ]}
    install_fake_session(monkeypatch, [
        route("GET", r"get-supplies", FakeResponse(200, json_data=payload)),
    ])
    supplies = await async_get_supplies("tok")
    assert len(supplies) == 2
    assert supplies[0]["distributor_name"] == "E-distribución"
    assert supplies[1]["distributor_name"] == "IDE"
    assert supplies[1]["point_type"] == 1


async def test_get_contract_detail_picks_latest(monkeypatch):
    payload = {"contract": [
        {"startDate": "2020-01-01", "contractedPowerkW": [3.0]},
        {"startDate": "2024-06-01", "contractedPowerkW": [4.6, 4.6]},
    ]}
    install_fake_session(monkeypatch, [
        route("GET", r"get-contract-detail", FakeResponse(200, json_data=payload)),
    ])
    contract = await datadis.async_get_contract_detail("tok", "CUPS", "2")
    assert contract["startDate"] == "2024-06-01"
