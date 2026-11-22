from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from fast_parser.catalog import FEDERAL_SUBJECT_CODES
from fast_parser.models import Article, Feed, FeedState, Source

MOSCOW = ZoneInfo("Europe/Moscow")


@dataclass
class ArticleFilters:
    source_id: str | None = None
    source_type: str | None = None
    region_code: str | None = None
    federal_district: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    text: str | None = None
    category: str | None = None
    sentiment: str | None = None
    fake_score_min: float | None = None
    fake_score_max: float | None = None
    limit: int = 50
    offset: int = 0


def search_articles(session: Session, filters: ArticleFilters) -> tuple[list[tuple[Article, Source]], int]:
    stmt = select(Article, Source).join(Source, Article.source_id == Source.id)
    stmt = _apply_filters(session, stmt, filters)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(
        stmt.order_by(Article.published_at.desc(), Article.id.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    ).all()
    return [(row[0], row[1]) for row in rows], int(total)


def _apply_filters(session: Session, stmt, filters: ArticleFilters):
    if filters.source_id:
        stmt = stmt.where(Source.id == filters.source_id)
    if filters.source_type:
        stmt = stmt.where(Source.type == filters.source_type)
    if filters.region_code:
        stmt = stmt.where(Source.region_code == filters.region_code)
    if filters.federal_district:
        stmt = stmt.where(Source.federal_district == filters.federal_district)
    if filters.category:
        stmt = stmt.where(Article.category == filters.category)
    if filters.sentiment:
        stmt = stmt.where(Article.sentiment == filters.sentiment)
    if filters.fake_score_min is not None:
        stmt = stmt.where(Article.fake_score >= filters.fake_score_min)
    if filters.fake_score_max is not None:
        stmt = stmt.where(Article.fake_score <= filters.fake_score_max)
    if filters.date_from is not None:
        stmt = stmt.where(Article.published_at >= _day_start(filters.date_from))
    if filters.date_to is not None:
        stmt = stmt.where(Article.published_at < _day_start(filters.date_to + timedelta(days=1)))
    needle = (filters.text or "").strip()
    if needle:
        folded = f"%{needle.casefold()}%"
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            title = func.py_lower(Article.title)
            body = func.py_lower(Article.text)
        else:
            title = func.lower(Article.title)
            body = func.lower(Article.text)
        stmt = stmt.where(or_(title.like(folded), body.like(folded)))
    return stmt


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=MOSCOW).astimezone(timezone.utc)


def filter_choices(session: Session) -> dict:
    sources = session.execute(select(Source.id, Source.name).order_by(Source.name)).all()
    types = session.scalars(select(Source.type).distinct().order_by(Source.type)).all()
    regions = session.execute(
        select(Source.region_code, Source.region)
        .where(Source.region_code.is_not(None))
        .distinct()
        .order_by(Source.region_code)
    ).all()
    districts = session.scalars(
        select(Source.federal_district)
        .where(Source.federal_district.is_not(None))
        .distinct()
        .order_by(Source.federal_district)
    ).all()
    return {
        "sources": sources,
        "types": types,
        "regions": regions,
        "districts": districts,
        "sentiments": ["positive", "neutral", "negative"],
    }


def article_with_source(session: Session, article_id: int) -> tuple[Article, Source] | None:
    row = session.execute(
        select(Article, Source)
        .join(Source, Article.source_id == Source.id)
        .where(Article.id == article_id)
    ).first()
    if row is None:
        return None
    return row[0], row[1]


def source_statuses(session: Session) -> list[dict]:
    sources = list(session.scalars(select(Source).order_by(Source.name)))
    feeds = list(session.scalars(select(Feed).order_by(Feed.id)))
    states = {state.feed_id: state for state in session.scalars(select(FeedState))}
    grouped: dict[str, list[Feed]] = {}
    for feed in feeds:
        grouped.setdefault(feed.source_id, []).append(feed)
    payload = []
    for source in sources:
        feed_rows = []
        for feed in grouped.get(source.id, []):
            state = states.get(feed.id)
            feed_rows.append(
                {
                    "url": feed.url,
                    "title": feed.title,
                    "enabled": feed.enabled,
                    "check_status": feed.check_status,
                    "last_status": state.last_status if state else None,
                    "last_error": state.last_error if state else None,
                    "last_success_at": state.last_success_at if state else None,
                    "last_http_status": state.last_http_status if state else None,
                    "consecutive_failures": state.consecutive_failures if state else 0,
                }
            )
        payload.append({"source": source, "feeds": feed_rows})
    return payload


def region_coverage(session: Session) -> list[dict]:
    names = {
        code: (name, district)
        for code, name, district in session.execute(
            select(Source.region_code, Source.region, Source.federal_district)
            .where(Source.region_code.is_not(None))
            .distinct()
        )
    }
    source_counts = dict(
        session.execute(
            select(Source.region_code, func.count())
            .where(Source.region_code.is_not(None))
            .group_by(Source.region_code)
        ).all()
    )
    telegram_counts = dict(
        session.execute(
            select(Source.region_code, func.count())
            .where(Source.region_code.is_not(None), Source.telegram_username.is_not(None))
            .group_by(Source.region_code)
        ).all()
    )
    rss_counts = dict(
        session.execute(
            select(Source.region_code, func.count(Feed.id))
            .join(Feed, Feed.source_id == Source.id)
            .where(Source.region_code.is_not(None), Feed.enabled.is_(True))
            .group_by(Source.region_code)
        ).all()
    )
    article_counts = dict(
        session.execute(
            select(Source.region_code, func.count(Article.id))
            .join(Article, Article.source_id == Source.id)
            .where(Source.region_code.is_not(None))
            .group_by(Source.region_code)
        ).all()
    )
    items = []
    for code in sorted(FEDERAL_SUBJECT_CODES):
        name, district = names.get(code, (None, None))
        sources = int(source_counts.get(code, 0))
        items.append(
            {
                "region_code": code,
                "region": name,
                "federal_district": district,
                "sources": sources,
                "rss_enabled": int(rss_counts.get(code, 0)),
                "telegram": int(telegram_counts.get(code, 0)),
                "articles": int(article_counts.get(code, 0)),
                "covered": sources > 0,
            }
        )
    return items


def dashboard(session: Session) -> dict:
    articles = session.scalar(select(func.count()).select_from(Article)) or 0
    sources = session.scalar(select(func.count()).select_from(Source)) or 0
    feeds_enabled = session.scalar(select(func.count()).select_from(Feed).where(Feed.enabled.is_(True))) or 0
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        day = func.strftime("%Y-%m-%d", Article.published_at, "+3 hours")
    else:
        day = func.to_char(Article.published_at + text("interval '3 hours'"), "YYYY-MM-DD")
    per_day = [
        {"key": str(key), "label": str(key), "count": int(count)}
        for key, count in session.execute(
            select(day, func.count())
            .where(Article.published_at.is_not(None))
            .group_by(day)
            .order_by(day)
        ).all()
        if key is not None
    ]
    per_source = [
        {"key": source_id, "label": name, "count": int(count)}
        for source_id, name, count in session.execute(
            select(Source.id, Source.name, func.count(Article.id))
            .join(Article, Article.source_id == Source.id)
            .group_by(Source.id, Source.name)
            .order_by(func.count(Article.id).desc(), Source.name)
        ).all()
    ]
    per_region = [
        {
            "key": code,
            "label": f"{code} {name}" if name else code,
            "count": int(count),
        }
        for code, name, count in session.execute(
            select(Source.region_code, Source.region, func.count(Article.id))
            .join(Article, Article.source_id == Source.id)
            .where(Source.region_code.is_not(None))
            .group_by(Source.region_code, Source.region)
            .order_by(Source.region_code)
        ).all()
    ]
    per_category = _named_counts(
        session.execute(
            select(Article.category, func.count())
            .where(Article.category.is_not(None))
            .group_by(Article.category)
            .order_by(func.count().desc(), Article.category)
        ).all()
    )
    per_sentiment = _named_counts(
        session.execute(
            select(Article.sentiment, func.count())
            .where(Article.sentiment.is_not(None))
            .group_by(Article.sentiment)
            .order_by(func.count().desc())
        ).all()
    )
    category_by_region = [
        {
            "key": f"{code}:{category}",
            "label": f"{code} {name or ''} · {category}".strip(),
            "count": int(count),
        }
        for code, name, category, count in session.execute(
            select(Source.region_code, Source.region, Article.category, func.count())
            .join(Article, Article.source_id == Source.id)
            .where(Source.region_code.is_not(None), Article.category.is_not(None))
            .group_by(Source.region_code, Source.region, Article.category)
            .order_by(func.count().desc())
            .limit(30)
        ).all()
    ]
    sentiment_by_source = [
        {
            "key": f"{source_id}:{sentiment}",
            "label": f"{name} · {sentiment}",
            "count": int(count),
        }
        for source_id, name, sentiment, count in session.execute(
            select(Source.id, Source.name, Article.sentiment, func.count())
            .join(Article, Article.source_id == Source.id)
            .where(Article.sentiment.is_not(None))
            .group_by(Source.id, Source.name, Article.sentiment)
            .order_by(func.count().desc())
            .limit(30)
        ).all()
    ]
    return {
        "articles": int(articles),
        "sources": int(sources),
        "feeds_enabled": int(feeds_enabled),
        "per_day": per_day,
        "per_source": per_source,
        "per_region": per_region,
        "per_category": per_category,
        "per_sentiment": per_sentiment,
        "category_by_region": category_by_region,
        "sentiment_by_source": sentiment_by_source,
    }


def _named_counts(rows) -> list[dict]:
    return [{"key": str(key), "label": str(key), "count": int(count)} for key, count in rows]
