from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fast_parser.catalog import Catalog, SourceSpec, feed_is_collected
from fast_parser.models import Article, Feed, FeedState, FetchLog, Source
from fast_parser.rss.parser import ParsedItem
from fast_parser.textutil import content_hash
from fast_parser.timeutil import utcnow
from fast_parser.urls import canonical_url


@dataclass
class UpsertResult:
    action: str
    article: Article | None = None


@dataclass
class RegionStat:
    region_code: str
    region: str
    federal_district: str
    sources: int
    articles: int


@dataclass
class DistrictStat:
    federal_district: str
    sources: int
    articles: int
    regions: int


# Official order of the federal districts, used by `stats`.
DISTRICT_ORDER = (
    "Центральный",
    "Северо-Западный",
    "Южный",
    "Северо-Кавказский",
    "Приволжский",
    "Уральский",
    "Сибирский",
    "Дальневосточный",
)


@dataclass
class StatsSnapshot:
    sources: int
    feeds: int
    feeds_enabled: int
    articles: int
    pending: int
    by_source: list[tuple[str, str, int]] = field(default_factory=list)
    last_success_at: object | None = None
    newest_published_at: object | None = None
    recent_errors: list[tuple[str, str]] = field(default_factory=list)
    telegram: int = 0
    federal_sources: int = 0
    regional_sources: int = 0
    regions_covered: int = 0
    by_district: list[DistrictStat] = field(default_factory=list)
    by_region: list[RegionStat] = field(default_factory=list)


def sync_catalog(
    session: Session,
    catalog: Catalog,
    *,
    skip_feed_urls: set[str],
    skip_source_ids: set[str],
) -> None:
    now = utcnow()
    for spec in catalog.sources:
        row = session.get(Source, spec.id)
        if row is None:
            row = Source(id=spec.id, updated_at=now)
            session.add(row)
        _apply_source(row, spec, now)
        seen: set[str] = set()
        for feed in spec.rss:
            seen.add(feed.url)
            feed_row = session.scalar(select(Feed).where(Feed.url == feed.url))
            if feed_row is None:
                feed_row = Feed(source_id=spec.id, url=feed.url)
                session.add(feed_row)
            feed_row.source_id = spec.id
            feed_row.title = feed.title
            feed_row.fulltext_hint = feed.fulltext
            feed_row.check_status = feed.check
            feed_row.note = feed.note
            feed_row.http_code = feed.http_code
            feed_row.meta = {
                "items": feed.items,
                "latest": feed.latest,
                "median_desc_chars": feed.median_desc_chars,
            }
            feed_row.enabled = feed_is_collected(
                feed,
                source_id=spec.id,
                skip_feed_urls=skip_feed_urls,
                skip_source_ids=skip_source_ids,
            )
        session.flush()
        for existing in session.scalars(select(Feed).where(Feed.source_id == spec.id)):
            if existing.url not in seen:
                existing.enabled = False
    session.commit()


def _apply_source(row: Source, spec: SourceSpec, now) -> None:
    row.name = spec.name
    row.type = spec.type
    row.site = spec.site
    row.status = spec.status
    row.notes = spec.notes
    row.region = spec.region
    row.region_code = spec.region_code
    row.federal_district = spec.federal_district
    row.updated_at = now
    telegram = spec.telegram
    if telegram is None:
        row.telegram_username = None
        row.telegram_title = None
        row.telegram_check = None
        row.telegram_meta = None
        return
    row.telegram_username = telegram.username
    row.telegram_title = telegram.title
    row.telegram_check = telegram.check
    row.telegram_meta = telegram.model_dump(exclude_none=True)


def enabled_feeds(session: Session) -> list[Feed]:
    return list(session.scalars(select(Feed).where(Feed.enabled.is_(True)).order_by(Feed.id)))


def load_state(session: Session, feed_id: int) -> FeedState | None:
    return session.get(FeedState, feed_id)


def upsert_article(
    session: Session,
    *,
    source_id: str,
    feed_id: int,
    item: ParsedItem,
    language: str | None,
) -> UpsertResult:
    canonical = canonical_url(item.url)
    digest = content_hash(item.title, item.text, canonical)
    now = utcnow()
    existing = session.scalar(select(Article).where(Article.canonical_url == canonical))
    if existing is None and item.guid:
        existing = session.scalar(
            select(Article).where(Article.feed_id == feed_id, Article.guid == item.guid)
        )
    if existing is None:
        same_body = session.scalar(
            select(Article).where(
                Article.source_id == source_id,
                Article.content_hash == digest,
            )
        )
        if same_body is not None:
            return UpsertResult("duplicate", same_body)
        article = Article(
            source_id=source_id,
            feed_id=feed_id,
            guid=item.guid,
            url=item.url,
            canonical_url=canonical,
            content_hash=digest,
            title=item.title,
            summary=item.summary or None,
            text=item.text,
            authors=list(item.authors),
            language=language or "ru",
            published_at=item.published_at,
            fetched_at=now,
            updated_at=now,
            content_origin=item.origin,
            pipeline_status="pending",
            extra={"categories": item.categories} if item.categories else None,
        )
        session.add(article)
        session.flush()
        return UpsertResult("new", article)

    if existing.canonical_url != canonical:
        clash = session.scalar(select(Article).where(Article.canonical_url == canonical))
        if clash is not None and clash.id != existing.id:
            return UpsertResult("duplicate", clash)
    same_payload = existing.content_hash == digest and existing.canonical_url == canonical
    if same_payload:
        return UpsertResult("duplicate", existing)

    hash_clash = session.scalar(
        select(Article).where(
            Article.source_id == source_id,
            Article.content_hash == digest,
            Article.id != existing.id,
        )
    )
    if hash_clash is not None:
        return UpsertResult("duplicate", hash_clash)

    existing.url = item.url
    existing.canonical_url = canonical
    existing.feed_id = feed_id
    existing.guid = item.guid or existing.guid
    existing.content_hash = digest
    existing.title = item.title
    existing.summary = item.summary or None
    existing.text = item.text
    existing.authors = list(item.authors)
    if item.published_at is not None:
        existing.published_at = item.published_at
    existing.updated_at = now
    existing.content_origin = item.origin
    existing.pipeline_status = "pending"
    existing.category = None
    existing.sentiment = None
    existing.sentiment_score = None
    existing.fake_score = None
    existing.extra = {"categories": item.categories} if item.categories else None
    session.flush()
    return UpsertResult("updated", existing)


def touch_feed_state(
    session: Session,
    *,
    feed_id: int,
    status: str,
    http_status: int | None,
    error: str | None,
    etag: str | None,
    last_modified: str | None,
    items_seen: int,
    started_at,
    newest_published_at,
    newest_guid: str | None,
    newest_url: str | None,
    keep_validators: bool,
) -> FeedState:
    state = session.get(FeedState, feed_id)
    if state is None:
        state = FeedState(feed_id=feed_id, consecutive_failures=0, items_seen=0)
        session.add(state)
    state.last_attempt_at = started_at
    state.last_status = status
    state.last_http_status = http_status
    state.last_error = error
    if status in {"success", "empty", "not_modified"}:
        state.consecutive_failures = 0
        state.last_success_at = utcnow()
    else:
        state.consecutive_failures = (state.consecutive_failures or 0) + 1
    if not keep_validators:
        state.etag = etag
        state.last_modified = last_modified
        state.items_seen = items_seen
    if newest_published_at is not None and (
        state.newest_published_at is None or newest_published_at > state.newest_published_at
    ):
        state.newest_published_at = newest_published_at
        state.newest_guid = newest_guid
        state.newest_url = newest_url
    return state


def add_fetch_log(
    session: Session,
    *,
    feed_id: int,
    started_at,
    finished_at,
    status: str,
    http_status: int | None,
    error: str | None,
    items_fetched: int,
    items_new: int,
    items_duplicate: int,
    items_updated: int,
) -> None:
    session.add(
        FetchLog(
            feed_id=feed_id,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            http_status=http_status,
            error=error,
            items_fetched=items_fetched,
            items_new=items_new,
            items_duplicate=items_duplicate,
            items_updated=items_updated,
        )
    )


def snapshot(session: Session, *, error_limit: int = 8) -> StatsSnapshot:
    sources = session.scalar(select(func.count()).select_from(Source)) or 0
    feeds = session.scalar(select(func.count()).select_from(Feed)) or 0
    enabled = session.scalar(select(func.count()).select_from(Feed).where(Feed.enabled.is_(True))) or 0
    articles = session.scalar(select(func.count()).select_from(Article)) or 0
    telegram = (
        session.scalar(
            select(func.count()).select_from(Source).where(Source.telegram_username.is_not(None))
        )
        or 0
    )
    pending = (
        session.scalar(
            select(func.count()).select_from(Article).where(Article.pipeline_status == "pending")
        )
        or 0
    )
    by_source = list(
        session.execute(
            select(Source.id, Source.name, func.count(Article.id))
            .join(Article, Article.source_id == Source.id, isouter=True)
            .group_by(Source.id, Source.name)
            .order_by(func.count(Article.id).desc(), Source.id)
        ).all()
    )
    last_success = session.scalar(select(func.max(FeedState.last_success_at)))
    newest = session.scalar(select(func.max(Article.published_at)))
    error_rows = session.execute(
        select(Feed.url, FeedState.last_error)
        .join(FeedState, FeedState.feed_id == Feed.id)
        .where(FeedState.last_status == "error")
        .order_by(FeedState.last_attempt_at.desc())
        .limit(error_limit)
    ).all()
    federal_sources = (
        session.scalar(select(func.count()).select_from(Source).where(Source.region_code.is_(None)))
        or 0
    )
    regional_sources = (
        session.scalar(
            select(func.count()).select_from(Source).where(Source.region_code.is_not(None))
        )
        or 0
    )
    by_region, by_district = _region_breakdown(session)
    return StatsSnapshot(
        sources=sources,
        feeds=feeds,
        feeds_enabled=enabled,
        articles=articles,
        pending=pending,
        by_source=[(row[0], row[1], row[2]) for row in by_source],
        last_success_at=last_success,
        newest_published_at=newest,
        recent_errors=[(row[0], row[1] or "") for row in error_rows],
        telegram=telegram,
        federal_sources=federal_sources,
        regional_sources=regional_sources,
        regions_covered=len(by_region),
        by_district=by_district,
        by_region=by_region,
    )


def _region_breakdown(session: Session) -> tuple[list[RegionStat], list[DistrictStat]]:
    source_rows = session.execute(
        select(
            Source.region_code,
            Source.region,
            Source.federal_district,
            func.count(Source.id),
        )
        .where(Source.region_code.is_not(None))
        .group_by(Source.region_code, Source.region, Source.federal_district)
        .order_by(Source.region_code)
    ).all()
    article_by_code = dict(
        session.execute(
            select(Source.region_code, func.count(Article.id))
            .join(Article, Article.source_id == Source.id)
            .where(Source.region_code.is_not(None))
            .group_by(Source.region_code)
        ).all()
    )
    article_by_district = dict(
        session.execute(
            select(Source.federal_district, func.count(Article.id))
            .join(Article, Article.source_id == Source.id)
            .where(Source.federal_district.is_not(None))
            .group_by(Source.federal_district)
        ).all()
    )
    by_region = [
        RegionStat(
            region_code=code,
            region=name or "",
            federal_district=district or "",
            sources=count,
            articles=int(article_by_code.get(code, 0)),
        )
        for code, name, district, count in source_rows
    ]
    district_bucket: dict[str, DistrictStat] = {}
    for item in by_region:
        bucket = district_bucket.get(item.federal_district)
        if bucket is None:
            bucket = DistrictStat(
                federal_district=item.federal_district,
                sources=0,
                articles=int(article_by_district.get(item.federal_district, 0)),
                regions=0,
            )
            district_bucket[item.federal_district] = bucket
        bucket.sources += item.sources
        bucket.regions += 1
    known = {name: index for index, name in enumerate(DISTRICT_ORDER)}
    by_district = sorted(
        district_bucket.values(),
        key=lambda item: (known.get(item.federal_district, len(known)), item.federal_district),
    )
    return by_region, by_district


def list_articles(
    session: Session,
    *,
    region_code: str | None = None,
    federal_district: str | None = None,
) -> list[Article]:
    """Articles whose source belongs to a subject or a federal district."""
    stmt = select(Article).join(Source, Article.source_id == Source.id)
    if region_code is not None:
        stmt = stmt.where(Source.region_code == region_code)
    if federal_district is not None:
        stmt = stmt.where(Source.federal_district == federal_district)
    stmt = stmt.order_by(Article.published_at, Article.id)
    return list(session.scalars(stmt))


def iter_export_dicts(session: Session):
    rows = session.execute(
        select(Article, Source)
        .join(Source, Article.source_id == Source.id)
        .order_by(Article.published_at, Article.id)
    )
    for article, source in rows:
        yield article.to_export_dict(
            source.name,
            region=source.region,
            region_code=source.region_code,
            federal_district=source.federal_district,
        )
