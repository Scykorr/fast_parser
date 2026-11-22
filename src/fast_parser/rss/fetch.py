from __future__ import annotations

import logging
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class HttpGetter(Protocol):
    async def get(self, url: str, *, headers: dict[str, str], timeout: httpx.Timeout, follow_redirects: bool) -> httpx.Response:
        ...


def format_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return f"{type(exc).__name__}: {text}"[:500]


async def fetch_with_retries(
    client: HttpGetter,
    url: str,
    *,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    retries: int,
    backoff_seconds: float,
    backoff_multiplier: float,
) -> httpx.Response:
    """GET with backoff. Timeouts, TLS/transport failures and 429/5xx are retried.

    The last response is returned even when it is still an error, so the caller
    can log the status and move on to the next feed. Transport errors that
    survive the last attempt are re-raised.
    """
    delay = backoff_seconds
    attempts = max(retries, 0) + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            log.warning("fetch %s attempt %s/%s failed: %s", url, attempt, attempts, format_error(exc))
            if attempt == attempts:
                raise
            await _sleep(delay)
            delay *= backoff_multiplier
            continue
        if response.status_code in RETRY_STATUSES and attempt < attempts:
            log.warning(
                "fetch %s attempt %s/%s HTTP %s",
                url,
                attempt,
                attempts,
                response.status_code,
            )
            await _sleep(delay)
            delay *= backoff_multiplier
            continue
        return response
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"fetch produced no response: {url}")


async def _sleep(seconds: float) -> None:
    if seconds > 0:
        import asyncio

        await asyncio.sleep(seconds)
