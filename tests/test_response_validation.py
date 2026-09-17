"""Response-validation hardening tests (audit T3/T4): garbage payloads must not crash."""
import datetime

import pytest

from custom_components.enchufado import cnmc, datadis, ree
from custom_components.enchufado.datadis import Datadis

from fakes import FakeResponse, install_fake_session, route

MONTH = datetime.date(2026, 5, 1)
LAST = datetime.date(2026, 5, 31)


def _setup_datadis():
    Datadis.setup("u", "p", "CUPS-TEST", "2", 1)


# --- Datadis login token sanity -------------------------------------------------

async def test_login_rejects_html_body_as_token(monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth",
              FakeResponse(200, text="<!DOCTYPE html><html>login</html>")),
    ])
    assert await datadis.async_login("u", "p") is None


async def test_login_rejects_empty_token(monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", FakeResponse(200, text="   ")),
    ])
    assert await datadis.async_login("u", "p") is None


async def test_login_accepts_plain_token(monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", FakeResponse(200, text="  tok-abc \n")),
    ])
    assert await datadis.async_login("u", "p") == "tok-abc"


# --- Datadis _request JSON validity ----------------------------------------------

async def test_200_non_json_body_fails_fast(monkeypatch):
    """A WAF HTML page behind a 200 must not burn the retry budget."""
    slept = []

    async def record(s):
        slept.append(s)

    monkeypatch.setattr(datadis, "_sleep", record)
    install_fake_session(monkeypatch, [
        route("GET", r"get-supplies", FakeResponse(200, text="<html>blocked</html>")),
    ])
    supplies = await datadis.async_get_supplies("tok")
    assert supplies == []
    assert slept == []  # no retries


# --- Datadis supplies/contract guards --------------------------------------------

async def test_supplies_skips_malformed_items(monkeypatch):
    install_fake_session(monkeypatch, [
        route("GET", r"get-supplies", FakeResponse(200, json_data={
            "supplies": [
                {"cups": "OK1", "pointType": 1, "distributorCode": 2},
                {"cups": "BAD", "pointType": "??", "distributorCode": 2},
                "not-a-dict",
                {"cups": "", "pointType": 1, "distributorCode": 2},
            ]
        })),
    ])
    supplies = await datadis.async_get_supplies("tok")
    assert [s["cups"] for s in supplies] == ["OK1"]


async def test_supplies_non_list_payload(monkeypatch):
    install_fake_session(monkeypatch, [
        route("GET", r"get-supplies", FakeResponse(200, json_data={"detail": "quota exceeded"})),
    ])
    assert await datadis.async_get_supplies("tok") == []


async def test_contract_non_list_payload(monkeypatch):
    install_fake_session(monkeypatch, [
        route("GET", r"get-contract-detail", FakeResponse(200, json_data={"error": "boom"})),
    ])
    assert await datadis.async_get_contract_detail("tok", "C", "2") is None


# --- Datadis timeCurve guards ----------------------------------------------------

async def test_consumptions_rejects_negative_and_nan(monkeypatch):
    _setup_datadis()
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", FakeResponse(200, text="tok")),
        route("GET", r"get-consumption-data", FakeResponse(200, json_data=[
            {"date": "2026/05/01", "time": "01:00", "consumptionKWh": "-5", "obtainMethod": "Real"},
            {"date": "2026/05/01", "time": "02:00", "consumptionKWh": float("nan"), "obtainMethod": "Real"},
            {"date": "2026/05/01", "time": "03:00", "consumptionKWh": True, "obtainMethod": "Real"},
            {"date": "2026/05/01", "time": "04:00", "consumptionKWh": "1.5", "obtainMethod": "Real"},
        ])),
    ])
    result = await Datadis.consumptions(MONTH, LAST)
    assert len(result) == 1  # only the valid 1.5 kWh hour survives
    assert next(iter(result.values()))["value"] == 1.5


async def test_consumptions_non_list_payload(monkeypatch):
    _setup_datadis()
    install_fake_session(monkeypatch, [
        route("POST", r"nikola-auth", FakeResponse(200, text="tok")),
        route("GET", r"get-consumption-data", FakeResponse(200, json_data={"message": "server error"})),
    ])
    assert await Datadis.consumptions(MONTH, LAST) == {}


# --- REE payload structure -------------------------------------------------------

async def test_ree_missing_indicator_is_hard_failure(monkeypatch):
    install_fake_session(monkeypatch, [
        route("GET", r"api\.esios\.ree\.es", FakeResponse(200, json_data={"errors": ["invalid token"]})),
    ])
    assert await ree.REE.pvpc(MONTH, LAST, "tok") is None


async def test_ree_skips_malformed_values(monkeypatch):
    install_fake_session(monkeypatch, [
        route("GET", r"api\.esios\.ree\.es", FakeResponse(200, json_data={
            "indicator": {"values": [
                {"datetime": "2026-05-01T00:00:00+02:00", "value": 123.45},
                {"datetime": "not-a-date", "value": 1},
                {"datetime": "2026-05-01T02:00:00+02:00", "value": None},
                {"datetime": "2026-05-01T03:00:00+02:00", "value": float("nan")},
            ]}
        })),
    ])
    result = await ree.REE.pvpc(MONTH, LAST, "tok")
    assert len(result) == 1
    assert result[next(iter(result))] == 0.12345


async def test_ree_200_non_json(monkeypatch):
    install_fake_session(monkeypatch, [
        route("GET", r"api\.esios\.ree\.es", FakeResponse(200, text="<html>502 proxy</html>")),
    ])
    assert await ree.REE.pvpc(MONTH, LAST, "tok") is None


# --- CNMC bill response ----------------------------------------------------------

def _period():
    return {
        "start_date": MONTH,
        "end_date": LAST,
        "power_high": 4.6,
        "power_low": 4.6,
    }


def _full_month_consumptions():
    import zoneinfo
    madrid = zoneinfo.ZoneInfo("Europe/Madrid")
    out = {}
    for day in range(1, 32):
        for hour in range(24):
            dt = datetime.datetime(2026, 5, day, hour, tzinfo=datetime.UTC)
            out[int(dt.astimezone(madrid).timestamp())] = 0.5
    return out


async def test_cnmc_bill_non_200_keeps_period(monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"curvaConsumo", FakeResponse(200, text="ABC123-curve")),
        route("GET", r"ofertas/pvpc", FakeResponse(500, text="error")),
    ])
    period, _ = await cnmc.calculate_bill(_period(), "CUPS", _full_month_consumptions(), "28001")
    assert "total_cost" not in period


async def test_cnmc_bill_garbage_json_keeps_period(monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"curvaConsumo", FakeResponse(200, text="ABC123-curve")),
        route("GET", r"ofertas/pvpc", FakeResponse(200, json_data={"graficoGastoTotalActual": {"hola": 1}})),
    ])
    period, _ = await cnmc.calculate_bill(_period(), "CUPS", _full_month_consumptions(), "28001")
    assert "total_cost" not in period


async def test_cnmc_bill_non_list_json(monkeypatch):
    install_fake_session(monkeypatch, [
        route("POST", r"curvaConsumo", FakeResponse(200, text="ABC123-curve")),
        route("GET", r"ofertas/pvpc", FakeResponse(200, json_data=[1, 2, 3])),
    ])
    period, _ = await cnmc.calculate_bill(_period(), "CUPS", _full_month_consumptions(), "28001")
    assert "total_cost" not in period
