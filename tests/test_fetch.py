import asyncio

import httpx
import pytest

from fast_parser.rss.fetch import fetch_with_retries


class _Script:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.headers = []

    async def get(self, url, *, headers, timeout, follow_redirects):
        self.calls += 1
        self.headers.append(headers)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run(outcomes, retries=1):
    script = _Script(outcomes)
    response = asyncio.run(
        fetch_with_retries(
            script,
            "https://alpha.example/rss.xml",
            headers={"User-Agent": "test"},
            timeout=httpx.Timeout(1),
            retries=retries,
            backoff_seconds=0,
            backoff_multiplier=1,
        )
    )
    return script, response


def test_retries_transport_error_then_succeeds():
    script, response = _run([httpx.ConnectError("TLS unexpected eof"), httpx.Response(200, content=b"<rss/>")])
    assert script.calls == 2
    assert response.status_code == 200


def test_retries_http_503_then_returns_last_error_status():
    script, response = _run([httpx.Response(503), httpx.Response(503)])
    assert script.calls == 2
    assert response.status_code == 503


def test_timeout_is_retried_and_then_raised():
    script = _Script([httpx.ReadTimeout("timed out"), httpx.ReadTimeout("timed out")])
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(
            fetch_with_retries(
                script,
                "https://alpha.example/rss.xml",
                headers={},
                timeout=httpx.Timeout(1),
                retries=1,
                backoff_seconds=0,
                backoff_multiplier=1,
            )
        )
    assert script.calls == 2


def test_404_is_not_retried():
    script, response = _run([httpx.Response(404)])
    assert script.calls == 1
    assert response.status_code == 404
