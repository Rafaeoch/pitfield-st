"""Fetching, caching and the narrowness of the retry.

A transient FRED timeout once failed an entire deploy while the same endpoint
answered in 0.19 seconds a minute later. The retry added for that has to stay
narrow: retrying a 404 wastes a free public source's goodwill and hides a real
error behind a delay.
"""

from __future__ import annotations

import requests

from pipeline.ingest import sources


class _Response:
    def __init__(self, status: int, body: bytes = b"ok"):
        self.status_code = status
        self.content = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code}", response=self
            )


def _no_sleep(monkeypatch):
    monkeypatch.setattr(sources.time, "sleep", lambda _: None)


def test_retries_a_timeout_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) < 3:
            raise requests.exceptions.ReadTimeout("slow")
        return _Response(200)

    monkeypatch.setattr(sources.requests, "get", fake_get)
    assert sources._get_with_retry("https://x/y", timeout=5).status_code == 200
    assert len(calls) == 3, "should have retried twice before succeeding"


def test_gives_up_after_the_bound_and_raises_the_real_error(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        raise requests.exceptions.ReadTimeout("always slow")

    monkeypatch.setattr(sources.requests, "get", fake_get)
    try:
        sources._get_with_retry("https://x/y", timeout=5)
    except requests.exceptions.ReadTimeout:
        pass
    else:  # pragma: no cover
        raise AssertionError("should have propagated the timeout")
    assert len(calls) == sources.MAX_ATTEMPTS, "must be bounded, not infinite"


def test_does_not_retry_a_404(monkeypatch):
    """A 404 is the source answering. It will answer the same way next time."""
    _no_sleep(monkeypatch)
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Response(404)

    monkeypatch.setattr(sources.requests, "get", fake_get)
    try:
        sources._get_with_retry("https://x/missing", timeout=5)
    except requests.exceptions.HTTPError:
        pass
    else:  # pragma: no cover
        raise AssertionError("should have raised on 404")
    assert len(calls) == 1, "a 404 must not be retried"


def test_retries_a_503(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Response(503 if len(calls) < 2 else 200)

    monkeypatch.setattr(sources.requests, "get", fake_get)
    assert sources._get_with_retry("https://x/y", timeout=5).status_code == 200
    assert len(calls) == 2


def test_cache_hit_makes_no_request_at_all(monkeypatch, tmp_path):
    """The first rule of this module: never re-hammer a free public source."""
    def explode(*a, **k):  # pragma: no cover
        raise AssertionError("a cached fetch must not touch the network")

    url = "https://example.invalid/data.csv"
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Response(200, b"payload"))
    first = sources.fetch(url, cache_dir=tmp_path)
    assert first.from_cache is False

    monkeypatch.setattr(sources.requests, "get", explode)
    second = sources.fetch(url, cache_dir=tmp_path)
    assert second.from_cache is True
    assert second.sha256 == first.sha256
