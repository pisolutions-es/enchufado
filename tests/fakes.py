"""Fake aiohttp plumbing for offline tests.

All HTTP in the integration goes through aiohttp.ClientSession; tests install
`install_fake_session` to route requests deterministically. Mocking at this
seam keeps tests valid when the client layer changes (shared session, retries).
"""
import re
from typing import Any, Callable

import aiohttp


class FakeResponse:
    """Minimal stand-in for aiohttp.ClientResponse (async context manager)."""

    def __init__(self, status: int = 200, json_data: Any = None, text: str = "",
                 headers: dict | None = None):
        self.status = status
        self._json = json_data
        self._text = text
        self.headers = headers or {}

    async def json(self, content_type: Any = None) -> Any:
        if self._json is None:
            raise aiohttp.ContentTypeError(None, None)
        return self._json

    async def text(self) -> str:
        return self._text if self._text else (
            str(self._json) if self._json is not None else ""
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """Routes requests to scripted responses; records every call made."""

    def __init__(self, routes: list, calls: list, closed: list | None = None):
        self._routes = routes
        self.calls = calls
        self._closed = closed if closed is not None else []

    def _match(self, method: str, url: str, params: dict | None, **kwargs):
        self.calls.append(
            {"method": method, "url": url, "params": params or {}, "kwargs": kwargs}
        )
        for route in self._routes:
            if route["method"] != method:
                continue
            if not re.search(route["url"], url):
                continue
            handler = route["handler"]
            if callable(handler):
                return handler(url=url, params=params or {}, kwargs=kwargs)
            return handler
        raise AssertionError(f"unexpected request: {method} {url} params={params}")

    def get(self, url, params=None, headers=None, **kwargs):
        return self._match("GET", str(url), params, headers=headers, **kwargs)

    def post(self, url, data=None, json=None, headers=None, **kwargs):
        return self._match(
            "POST", str(url), data if isinstance(data, dict) else None,
            json=json, headers=headers, **kwargs,
        )

    async def close(self):
        self._closed.append(True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    @property
    def closed(self):
        return bool(self._closed)


class FakeSessionManager:
    """Replacement for aiohttp.ClientSession(...) call — returns a FakeSession."""

    def __init__(self, session: FakeSession):
        self._session = session
        self.timeouts: list = []

    def __call__(self, *args, **kwargs):
        if "timeout" in kwargs:
            self.timeouts.append(kwargs["timeout"])
        return self._session


def route(method: str, url_pattern: str, handler) -> dict:
    """One routing rule: handler is a FakeResponse or a callable(request)->FakeResponse."""
    return {"method": method, "url": url_pattern, "handler": handler}


def install_fake_session(monkeypatch, routes: list) -> dict:
    """Patch aiohttp.ClientSession with a router. Returns {'calls': [...], 'manager': ...}."""
    calls: list = []
    closed: list = []
    session = FakeSession(routes, calls, closed)
    manager = FakeSessionManager(session)
    monkeypatch.setattr(aiohttp, "ClientSession", manager)
    return {"calls": calls, "session": session, "manager": manager, "closed": closed}
