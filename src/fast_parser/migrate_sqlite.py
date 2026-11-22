"""Copy an existing SQLite catalog into the current database, usually PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_parser.db import init_db
from fast_parser.models import Article, Feed, FeedState, Source
from fast_parser.storage import attach_lookup_ids


@dataclass
class MigrationStats:
    sources: int = 0
    feeds: int = 0
    articles: int = 0


def migrate_sqlite(sqlite_url: str, destination: Session) -> MigrationStats:
    source_factory = init_db(sqlite_url)
    origin = source_factory()
    stats = MigrationStats()
    try:
        feed_map: dict[int, int] = {}
        for source in origin.scalars(select(Source)):
            if destination.get(Source, source.id) is None:
                destination.add(
                    Source(
                        id=source.id,
                        nid=source.nid,
                        name=source.name,
                        type=source.type,
                        site=source.site,
                        status=source.status,
                        notes=source.notes,
                        region=source.region,
                        region_code=source.region_code,
                        federal_district=source.federal_district,
                        telegram_username=source.telegram_username,
                        telegram_title=source.telegram_title,
                        telegram_check=source.telegram_check,
                        telegram_meta=source.telegram_meta,
                        updated_at=source.updated_at,
                    )
                )
                stats.sources += 1
        destination.flush()
        for feed in origin.scalars(select(Feed)):
            existing = destination.scalar(select(Feed).where(Feed.url == feed.url))
            if existing is None:
                existing = Feed(
                    source_id=feed.source_id,
                    url=feed.url,
                    title=feed.title,
                    fulltext_hint=feed.fulltext_hint,
                    check_status=feed.check_status,
                    enabled=feed.enabled,
                    note=feed.note,
                    http_code=feed.http_code,
                    meta=feed.meta,
                )
                destination.add(existing)
                destination.flush()
                stats.feeds += 1
            feed_map[feed.id] = existing.id
            state = origin.get(FeedState, feed.id)
            if state is not None and destination.get(FeedState, existing.id) is None:
                destination.add(
                    FeedState(
                        feed_id=existing.id,
                        etag=state.etag,
                        last_modified=state.last_modified,
                        last_success_at=state.last_success_at,
                        last_attempt_at=state.last_attempt_at,
                        last_status=state.last_status,
                        last_error=state.last_error,
                        last_http_status=state.last_http_status,
                        consecutive_failures=state.consecutive_failures,
                        newest_published_at=state.newest_published_at,
                        newest_guid=state.newest_guid,
                        newest_url=state.newest_url,
                        items_seen=state.items_seen,
                    )
                )
        for article in origin.scalars(select(Article)):
            if destination.scalar(select(Article.id).where(Article.canonical_url == article.canonical_url)):
                continue
            copy = Article(
                source_id=article.source_id,
                feed_id=feed_map.get(article.feed_id) if article.feed_id else None,
                guid=article.guid,
                url=article.url,
                canonical_url=article.canonical_url,
                content_hash=article.content_hash,
                title=article.title,
                summary=article.summary,
                text=article.text,
                authors=article.authors,
                language=article.language,
                published_at=article.published_at,
                fetched_at=article.fetched_at,
                updated_at=article.updated_at,
                category=article.category,
                sentiment=article.sentiment,
                sentiment_score=article.sentiment_score,
                fake_score=article.fake_score,
                content_origin=article.content_origin,
                pipeline_status=article.pipeline_status,
                extra=article.extra,
            )
            attach_lookup_ids(destination, copy)
            destination.add(copy)
            stats.articles += 1
        destination.commit()
        return stats
    finally:
        origin.close()
