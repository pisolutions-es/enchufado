"""Unit tests for the CNMC bill simulator."""
import base64
import datetime
import re

import pytest

from custom_components.enchufado import cnmc
from custom_components.enchufado.cnmc import calculate_bill
from custom_components.enchufado.util import madrid_timestamp

from fakes import FakeResponse, install_fake_session, route


def _period(month_first=datetime.date(2026, 1, 1), days=31):
    return {
        "start_date": month_first,
        "end_date": month_first + datetime.timedelta(days=days - 1),
        "power_high": 4.6,
        "power_low": 4.6,
    }


def _consumptions(month_first=datetime.date(2026, 1, 1), hours=24 * 31, value=0.5):
    start = madrid_timestamp(month_first)
    return {start + i * 3600: value for i in range(hours)}


async def test_too_few_points_skipped(monkeypatch):
    period = _period()
    fake = install_fake_session(monkeypatch, [])
    result, csv = await calculate_bill(period, "CUPS", _consumptions(hours=24), "28001")
    assert result is period
    assert csv is None
    assert fake["calls"] == []  # no HTTP at all


async def test_period_must_match_data_range(monkeypatch):
    period = _period()
    install_fake_session(monkeypatch, [])
    _, csv = await calculate_bill(period, "CUPS", _consumptions(hours=100), "28001")
    assert csv is None


async def test_gap_over_2h_skipped(monkeypatch):
    period = _period(days=2)
    cons = _consumptions(hours=48)
    keys = sorted(cons)
    cons.pop(keys[25])
    cons.pop(keys[26])
    install_fake_session(monkeypatch, [])
    _, csv = await calculate_bill(period, "CUPS", cons, "28001")
    assert csv is None


async def test_bill_success_fills_cost_fields(monkeypatch):
    period = _period(days=2)
    bill = {"graficoGastoTotalActual": {
        "importeTotal": 33.33, "importePotencia": 10.0, "importeEnergia": 20.0,
        "importeAlquiler": 2.0, "importeIVA": 1.33,
    }}
    install_fake_session(monkeypatch, [
        route("POST", r"cargar/curvaConsumo", FakeResponse(200, text="XYZ123-rest")),
        route("GET", r"ofertas/pvpc", FakeResponse(200, json_data=bill)),
    ])
    result, csv = await calculate_bill(period, "CUPS", _consumptions(hours=48), "28001")
    assert result["total_cost"] == 33.33
    assert result["power_cost"] == 10.0
    assert result["total_consumption"] == pytest.approx(24.0)
    # CSV payload: CUPS;date;hour(1-24);comma-decimal;R
    header, first = csv.split("\r\n")[0], csv.split("\r\n")[1]
    assert header.startswith("CUPS;Fecha;Hora")
    parts = first.split(";")
    assert parts[0] == "CUPS"
    assert 1 <= int(parts[2]) <= 24
    assert "," in parts[3]


async def test_old_period_marks_cost_dash(monkeypatch):
    period = _period(days=2)
    install_fake_session(monkeypatch, [
        route("POST", r"cargar/curvaConsumo",
              FakeResponse(200, text=cnmc._MSG_OLD_FILE)),
    ])
    result, _ = await calculate_bill(period, "CUPS", _consumptions(hours=48), "28001")
    assert result["total_cost"] == "-"


async def test_no_data_warning_no_cost(monkeypatch):
    period = _period(days=2)
    install_fake_session(monkeypatch, [
        route("POST", r"cargar/curvaConsumo",
              FakeResponse(200, text=cnmc._MSG_NO_DATA)),
    ])
    result, csv = await calculate_bill(period, "CUPS", _consumptions(hours=48), "28001")
    assert "total_cost" not in result
    assert csv is not None


async def test_upload_network_error_swallowed(monkeypatch):
    period = _period(days=2)

    def boom(**_):
        raise OSError("dns fail")

    install_fake_session(monkeypatch, [route("POST", r"cargar", boom)])
    result, _ = await calculate_bill(period, "CUPS", _consumptions(hours=48), "28001")
    assert "total_cost" not in result  # no crash, no cost


async def test_bill_dates_from_daily_consumo(monkeypatch):
    period = _period(days=2)
    bill = {
        "graficoGastoTotalActual": {
            "importeTotal": 10.0, "importePotencia": 5.0, "importeEnergia": 3.0,
            "importeAlquiler": 1.0, "importeIVA": 1.0,
        },
        "graficaConsumoDiario": {"consumosDiarios": [
            {"fecha": "31/12/2025"}, {"fecha": "01/02/2026"},
        ]},
    }
    install_fake_session(monkeypatch, [
        route("POST", r"cargar/curvaConsumo", FakeResponse(200, text="AB9-x")),
        route("GET", r"ofertas/pvpc", FakeResponse(200, json_data=bill)),
    ])
    result, _ = await calculate_bill(period, "CUPS", _consumptions(hours=48), "28001")
    assert result["start_date"] == datetime.date(2025, 12, 31)
    assert result["end_date"] == datetime.date(2026, 2, 1)
