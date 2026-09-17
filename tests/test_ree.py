"""Unit tests for the REE/ESIOS price client."""
import datetime

from custom_components.enchufado.ree import REE
from custom_components.enchufado.util import MADRID_TZ

from fakes import FakeResponse, install_fake_session, route


async def test_pvpc_parses_values(monkeypatch):
    payload = {"indicator": {"values": [
        {"datetime": "2026-03-01T00:00:00+01:00", "value": 123.456},
        {"datetime": "2026-03-01T01:00:00+01:00", "value": -5.5},
    ]}}
    fake = install_fake_session(monkeypatch, [
        route("GET", r"api\.esios\.ree\.es", FakeResponse(200, json_data=payload)),
    ])
    result = await REE.pvpc(datetime.date(2026, 3, 1), datetime.date(2026, 3, 1), "token")
    assert len(result) == 2
    values = sorted(result.values())
    assert values == [-0.0055, 0.12346]  # MWh→€/kWh, rounded; negative price kept
    # negative-price timestamp present
    ts = next(t for t, v in result.items() if v < 0)
    assert datetime.datetime.fromtimestamp(ts, MADRID_TZ).hour == 1


async def test_pvpc_non_200_returns_none(monkeypatch):
    install_fake_session(monkeypatch, [
        route("GET", r"api\.esios\.ree\.es", FakeResponse(429, text="rate limited")),
    ])
    assert await REE.pvpc(datetime.date(2026, 3, 1), datetime.date(2026, 3, 1), "t") is None


async def test_pvpc_sends_token_header(monkeypatch):
    payload = {"indicator": {"values": []}}
    fake = install_fake_session(monkeypatch, [
        route("GET", r"api\.esios\.ree\.es", FakeResponse(200, json_data=payload)),
    ])
    await REE.pvpc(datetime.date(2026, 3, 1), datetime.date(2026, 3, 1), "secret-token")
    headers = fake["calls"][0]["kwargs"]["headers"]
    assert headers["x-api-key"] == "secret-token"
