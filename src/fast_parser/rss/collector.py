from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from fast_parser.config import AppSettings
from fast_parser.rss.extract import TextExtractor
from fast_parser.rss.fetch import fetch_with_retries, format_error
from fast_parser.rss.parser import ParsedItem, parse_feed
from fast_parser.textutil import normalize_ws
from fast_parser.timeutil import utcnow

log = logging.getLogger(__name__)


@dataclass
class FeedCursor:
    feed_id: int
    source_id: str
    url: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class FetchOutcome:
    feed_id: int
    source_id: str
    url: str
    status: str
    http_status: int | None = None
    error: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    items: list[ParsedItem] = field(default_factory=list)
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime = field(default_factory=utcnow)


async def fetch_feed(
    client: httpx.AsyncClient,
    cursor: FeedCursor,
    settings: AppSettings,
    *,
    extractor: TextExtractor,
    semaphore: asyncio.Semaphore,
    host_locks: dict[str, asyncio.Semaphore],
) -> FetchOutcome:
    started = utcnow()
    host = urlsplit(cursor.url).hostname or ""
    host_lock = host_locks[host]
    headers = _request_headers(settings, cursor)
    timeout = httpx.Timeout(settings.http.timeout_seconds)
    try:
        async with semaphore:
            async with host_lock:
                response = await fetch_with_retries(
                    client,
                    cursor.url,
                    headers=headers,
                    timeout=timeout,
                    retries=settings.http.retries,
                    backoff_seconds=settings.http.backoff_seconds,
                    backoff_multiplier=settings.http.backoff_multiplier,
                )
    except Exception as exc:
        log.warning("feed %s failed: %s", cursor.url, format_error(exc))
        return FetchOutcome(
            feed_id=cursor.feed_id,
            source_id=cursor.source_id,
            url=cursor.url,
            status="error",
            error=format_error(exc),
            etag=cursor.etag,
            last_modified=cursor.last_modified,
            started_at=started,
            finished_at=utcnow(),
        )

    if response.status_code == 304:
        log.info("feed %s not modified", cursor.url)
        return FetchOutcome(
            feed_id=cursor.feed_id,
            source_id=cursor.source_id,
            url=cursor.url,
            status="not_modified",
            http_status=304,
            etag=cursor.etag,
            last_modified=cursor.last_modified,
            started_at=started,
            finished_at=utcnow(),
        )

    etag = response.headers.get("etag")
    last_modified = response.headers.get("last-modified")
    if response.status_code >= 400:
        error = f"HTTP {response.status_code}"
        log.warning("feed %s failed: %s", cursor.url, error)
        return FetchOutcome(
            feed_id=cursor.feed_id,
            source_id=cursor.source_id,
            url=cursor.url,
            status="error",
            http_status=response.status_code,
            error=error,
            etag=cursor.etag,
            last_modified=cursor.last_modified,
            started_at=started,
            finished_at=utcnow(),
        )

    body = response.content
    if len(body) > settings.collector.max_feed_bytes:
        error = f"response too large: {len(body)} bytes"
        log.warning("feed %s failed: %s", cursor.url, error)
        return FetchOutcome(
            feed_id=cursor.feed_id,
            source_id=cursor.source_id,
            url=cursor.url,
            status="error",
            http_status=response.status_code,
            error=error,
            started_at=started,
            finished_at=utcnow(),
        )

    try:
        items = parse_feed(body, naive_timezone=settings.dates.naive_timezone)
    except Exception as exc:
        log.warning("feed %s parse failed: %s", cursor.url, format_error(exc))
        return FetchOutcome(
            feed_id=cursor.feed_id,
            source_id=cursor.source_id,
            url=cursor.url,
            status="error",
            http_status=response.status_code,
            error=format_error(exc),
            started_at=started,
            finished_at=utcnow(),
        )

    if settings.collector.fetch_fulltext and items:
        await _enrich_short_items(
            client,
            items,
            settings,
            extractor=extractor,
            semaphore=semaphore,
            host_locks=host_locks,
        )

    status = "success" if items else "empty"
    log.info("feed %s %s items=%s", cursor.url, status, len(items))
    return FetchOutcome(
        feed_id=cursor.feed_id,
        source_id=cursor.source_id,
        url=cursor.url,
        status=status,
        http_status=response.status_code,
        etag=etag,
        last_modified=last_modified,
        items=items,
        started_at=started,
        finished_at=utcnow(),
    )


def _request_headers(settings: AppSettings, cursor: FeedCursor) -> dict[str, str]:
    headers = {
        "User-Agent": settings.http.user_agent,
        "Accept": settings.http.accept,
        "Accept-Language": settings.http.accept_language,
    }
    if cursor.etag:
        headers["If-None-Match"] = cursor.etag
    if cursor.last_modified:
        headers["If-Modified-Since"] = cursor.last_modified
    return headers


async def _enrich_short_items(
    client: httpx.AsyncClient,
    items: list[ParsedItem],
    settings: AppSettings,
    *,
    extractor: TextExtractor,
    semaphore: asyncio.Semaphore,
    host_locks: dict[str, asyncio.Semaphore],
) -> None:
    pending = [item for item in items if _needs_fulltext(item, settings)]

    async def one(item: ParsedItem) -> None:
        host = urlsplit(item.url).hostname or ""
        timeout = httpx.Timeout(settings.http.fulltext_timeout_seconds)
        try:
            async with semaphore:
                async with host_locks[host]:
                    response = await fetch_with_retries(
                        client,
                        item.url,
                        headers={
                            "User-Agent": settings.http.user_agent,
                            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": settings.http.accept_language,
                        },
                        timeout=timeout,
                        retries=settings.http.retries,
                        backoff_seconds=settings.http.backoff_seconds,
                        backoff_multiplier=settings.http.backoff_multiplier,
                    )
        except Exception as exc:
            log.warning("fulltext %s failed: %s", item.url, format_error(exc))
            return
        if response.status_code >= 400:
            log.warning("fulltext %s failed: HTTP %s", item.url, response.status_code)
            return
        if len(response.content) > settings.collector.max_article_bytes:
            log.warning("fulltext %s skipped: response too large", item.url)
            return
        try:
            html = response.text
            extracted = extractor.extract(html, str(response.url))
        except Exception as exc:
            log.warning("fulltext %s extract failed: %s", item.url, format_error(exc))
            return
        if not extracted:
            return
        cleaned = normalize_ws(extracted)
        if len(cleaned) > len(item.text):
            item.text = cleaned
            item.origin = "extracted"

    if pending:
        await asyncio.gather(*(one(item) for item in pending))


def _needs_fulltext(item: ParsedItem, settings: AppSettings) -> bool:
    if len(item.text) >= settings.collector.fulltext_min_chars:
        return False
    return item.url.startswith(("http://", "https://"))


def host_locks(per_host: int) -> dict[str, asyncio.Semaphore]:
    return defaultdict(lambda: asyncio.Semaphore(max(per_host, 1)))
