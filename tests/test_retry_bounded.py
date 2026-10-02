"""CRITICAL-1 regression tests: Retry-After retries must be bounded.

Before the fix, a server that kept answering 429/5xx WITH a Retry-After
header made `_request`/`async_login` loop forever (the header branch never
checked the attempt count), wedging the import task and the import_running
guard until an HA restart.
"""
import datetime

import pytest

from custom_components.enchufado import datadis
from custom_components.enchufado.datadis import Datadis, _retry_delay, async_login

from fakes import FakeResponse, install_fake_session, route


@pytest.fixture(autouse=True)
def reset_datadis_state(monkeypatch):
    Datadis.setup("u", "p", "cups", "2", 1)
    datadis._session = None
    datadis._quota_blocked_until = 0.0

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(datadis, "_sleep", no_sleep)
    yield
    datadis._session = None
    datadis._quota_blocked_until = 0.0


def test_retry_after_is_capped_by_attempt_budget():
    # The Retry-After branch must honor _MAX_RETRIES like the backoff branch.
    assert _retry_delay(0, "60") == 60.0
    assert _retry_delay(1, "86400") == 3600.0  # clamped to 1 h
    assert _retry_delay(datadis._MAX_RETRIES, "60") is None
    assert _retry_delay(datadis._MAX_RETRIES + 5, "60") is None


def test_backoff_branch_still_capped():
    assert _retry_delay(datadis._MAX_RETRIES, None) is None
    delay = _retry_delay(0, None)
    assert 0.5 <= delay <= 1.5


async def test_request_with_persistent_429_plus_retry_after_terminates(monkeypatch):
    """A 429 that always carries Retry-After must return after a bounded
    number of calls (not loop until the 1 h clamp elapses) and surface the
    quota signal so a repair issue is raised instead of a wedged import."""
    fake = install_fake_session(
        monkeypatch,
        [route("GET", r"get-consumption-data", lambda **_: FakeResponse(
            429, text="quota", headers={"Retry-After": "60"}))],
    )
    Datadis.setup("user", "pass", "ES-CUPS", "2", 1)
    Datadis._token = "tok"

    d = datetime.date(2026, 3, 1)
    result = await Datadis.consumptions(d, d)

    assert result == {}
    assert Datadis.last_error == "quota"
    assert len([c for c in fake["calls"] if c["method"] == "GET"]) == datadis._MAX_RETRIES + 1


async def test_request_with_persistent_500_plus_retry_after_terminates(monkeypatch):
    fake = install_fake_session(
        monkeypatch,
        [route("GET", r"get-consumption-data", lambda **_: FakeResponse(
            503, text="boom", headers={"Retry-After": "30"}))],
    )
    Datadis.setup("user", "pass", "ES-CUPS", "2", 1)
    Datadis._token = "tok"

    d = datetime.date(2026, 3, 1)
    result = await Datadis.consumptions(d, d)

    assert result == {}
    assert Datadis.last_error == "network"
    assert len([c for c in fake["calls"] if c["method"] == "GET"]) == datadis._MAX_RETRIES + 1


async def test_login_with_persistent_429_plus_retry_after_terminates(monkeypatch):
    fake = install_fake_session(
        monkeypatch,
        [route("POST", r"nikola-auth/tokens", lambda **_: FakeResponse(
            429, text="slow down", headers={"Retry-After": "60"}))],
    )

    token = await async_login("user", "pass")

    assert token is None
    posts = [c for c in fake["calls"] if c["method"] == "POST"]
    assert len(posts) == datadis._MAX_RETRIES + 1
