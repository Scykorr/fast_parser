from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from fast_parser.catalog import load_merged_catalog
from fast_parser.config import AppSettings
from fast_parser.db import init_db
from fast_parser.pipeline.runner import Pipeline
from fast_parser.pipeline.stubs import build_processors
from fast_parser.rss.collector import FeedCursor, FetchOutcome, fetch_feed, host_locks
from fast_parser.rss.extract import TextExtractor, TrafilaturaExtractor
from fast_parser.storage import (
    add_fetch_log,
    enabled_feeds,
    load_state,
    sync_catalog,
    touch_feed_state,
    upsert_article,
)
from fast_parser.urls import canonical_url

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    feeds_total: int = 0
    feeds_ok: int = 0
    feeds_not_modified: int = 0
    feeds_empty: int = 0
    feeds_error: int = 0
    items_fetched: int = 0
    items_new: int = 0
    items_duplicate: int = 0
    items_updated: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def open_session(settings: AppSettings) -> Session:
    paths = [settings.sources_path]
    if settings.regional_sources_path is not None:
        paths.append(settings.regional_sources_path)
    catalog = load_merged_catalog(paths)
    factory = init_db(settings.database.url)
    session = factory()
    sync_catalog(
        session,
        catalog,
        skip_feed_urls=set(settings.collector.skip_feed_urls),
        skip_source_ids=set(settings.collector.skip_source_ids),
    )
    return session


def run_once(settings: AppSettings) -> RunStats:
    session = open_session(settings)
    try:
        return asyncio.run(collect_enabled(settings, session))
    finally:
        session.close()


async def collect_enabled(
    settings: AppSettings,
    session: Session,
    *,
    client: httpx.AsyncClient | None = None,
    extractor: TextExtractor | None = None,
) -> RunStats:
    cursors = _cursors(session)
    log.info("collecting %s feeds", len(cursors))
    pipeline = None
    if settings.pipeline.enabled:
        try:
            processors = build_processors(settings.pipeline.processors)
        except KeyError as exc:
            message = exc.args[0] if exc.args else str(exc)
            raise ValueError(message) from exc
        pipeline = Pipeline(processors)
    text_extractor = extractor or TrafilaturaExtractor()
    semaphore = asyncio.Semaphore(max(settings.http.max_concurrency, 1))
    locks = host_locks(settings.http.per_host_concurrency)

    async def _run(http: httpx.AsyncClient) -> RunStats:
        async def one(cursor: FeedCursor) -> _FeedTally:
            outcome = await fetch_feed(
                http,
                cursor,
                settings,
                extractor=text_extractor,
                semaphore=semaphore,
                host_locks=locks,
            )
            return _persist(session, outcome, pipeline)

        tallies = await asyncio.gather(*(one(cursor) for cursor in cursors))
        return _combine(tallies)

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient(trust_env=settings.http.trust_env) as http:
        return await _run(http)


def _cursors(session: Session) -> list[FeedCursor]:
    cursors: list[FeedCursor] = []
    for feed in enabled_feeds(session):
        state = load_state(session, feed.id)
        cursors.append(
            FeedCursor(
                feed_id=feed.id,
                source_id=feed.source_id,
                url=feed.url,
                etag=state.etag if state else None,
                last_modified=state.last_modified if state else None,
            )
        )
    return cursors


@dataclass
class _FeedTally:
    status: str
    fetched: int = 0
    new: int = 0
    duplicate: int = 0
    updated: int = 0
    error: tuple[str, str] | None = None


def _persist(session: Session, outcome: FetchOutcome, pipeline: Pipeline | None) -> _FeedTally:
    new = duplicate = updated = 0
    newest = None
    newest_guid = None
    newest_url = None
    if outcome.status in {"success", "empty"}:
        for item in outcome.items:
            if item.published_at is not None and (newest is None or item.published_at > newest):
                newest = item.published_at
                newest_guid = item.guid
                newest_url = canonical_url(item.url)
            result = upsert_article(
                session,
                source_id=outcome.source_id,
                feed_id=outcome.feed_id,
                item=item,
                language=item.language,
            )
            if result.action == "new":
                new += 1
            elif result.action == "updated":
                updated += 1
            else:
                duplicate += 1
            if pipeline is not None and result.action in {"new", "updated"} and result.article is not None:
                pipeline.apply(result.article)
    keep_validators = outcome.status in {"error", "not_modified"}
    touch_feed_state(
        session,
        feed_id=outcome.feed_id,
        status=outcome.status,
        http_status=outcome.http_status,
        error=outcome.error,
        etag=outcome.etag,
        last_modified=outcome.last_modified,
        items_seen=len(outcome.items),
        started_at=outcome.started_at,
        newest_published_at=newest,
        newest_guid=newest_guid,
        newest_url=newest_url,
        keep_validators=keep_validators,
    )
    add_fetch_log(
        session,
        feed_id=outcome.feed_id,
        started_at=outcome.started_at,
        finished_at=outcome.finished_at,
        status=outcome.status,
        http_status=outcome.http_status,
        error=outcome.error,
        items_fetched=len(outcome.items),
        items_new=new,
        items_duplicate=duplicate,
        items_updated=updated,
    )
    session.commit()
    return _FeedTally(
        status=outcome.status,
        fetched=len(outcome.items),
        new=new,
        duplicate=duplicate,
        updated=updated,
        error=(outcome.url, outcome.error or "") if outcome.status == "error" else None,
    )


def _combine(tallies: list[_FeedTally]) -> RunStats:
    stats = RunStats(feeds_total=len(tallies))
    for tally in tallies:
        stats.items_fetched += tally.fetched
        stats.items_new += tally.new
        stats.items_duplicate += tally.duplicate
        stats.items_updated += tally.updated
        if tally.status == "success":
            stats.feeds_ok += 1
        elif tally.status == "not_modified":
            stats.feeds_not_modified += 1
        elif tally.status == "empty":
            stats.feeds_empty += 1
        else:
            stats.feeds_error += 1
            if tally.error:
                stats.errors.append(tally.error)
    return stats
