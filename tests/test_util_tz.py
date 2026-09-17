"""Tests for the shared tz helpers (audit T1: host-timezone independence)."""
import datetime
import os
import time
import types

from custom_components.enchufado import util
from custom_components.enchufado.util import MADRID_TZ, madrid_today, madrid_timestamp


def _datetime_module_now_fixed(pinned: datetime.datetime):
    """A `datetime` module stand-in whose datetime.now() is pinned."""
    class _DT:
        @staticmethod
        def now(tz=None):
            return pinned.astimezone(tz) if tz else pinned.replace(tzinfo=None)

    mod = types.SimpleNamespace(datetime=_DT)
    return mod


def test_madrid_today_follows_madrid_not_the_host(monkeypatch):
    """At 2026-10-31 23:30 UTC (CET, UTC+1), Madrid already has Nov 1; a UTC host says Oct 31."""
    fixed = datetime.datetime(2026, 10, 31, 23, 30, tzinfo=datetime.UTC)
    monkeypatch.setattr(util, "datetime", _datetime_module_now_fixed(fixed))
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        assert madrid_today() == datetime.date(2026, 11, 1)
        # the host-local naive date would have been a day behind
        assert fixed.replace(tzinfo=None).date() == datetime.date(2026, 10, 31)
    finally:
        time.tzset()


def test_madrid_timestamp_stable_under_utc_host():
    """madrid_timestamp must not depend on the host TZ (regression: time.mktime)."""
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        ts = madrid_timestamp(datetime.date(2026, 7, 15))
        # Madrid in July is UTC+2 → local midnight = 22:00 UTC the previous day.
        assert datetime.datetime.fromtimestamp(ts, datetime.UTC) == datetime.datetime(
            2026, 7, 14, 22, 0, tzinfo=datetime.UTC
        )
    finally:
        if saved is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = saved
        time.tzset()
