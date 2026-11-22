"""One filter model for the API, the CLI and the web UI.

Categorical fields accept several values and a separate exclude list.
A value in both lists is excluded. Dates follow Europe/Moscow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, literal_column, or_, select, text
from sqlalchemy.orm import Session

from fast_parser.models import Article, Source

MOSCOW = ZoneInfo("Europe/Moscow")
OFFICIAL_STATUSES = ("ok", "blocked_from_box_verified")
CHANNELS = ("rss", "telegram", "vk", "x")
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

_LIST_FIELDS = {
    "sources": "source_id",
    "sources_not": "source_id_not",
    "source_types": "source_type",
    "source_types_not": "source_type_not",
    "source_statuses": "source_status",
    "source_statuses_not": "source_status_not",
    "regions": "region",
    "regions_not": "region_not",
    "region_codes": "region_code",
    "region_codes_not": "region_code_not",
    "districts": "federal_district",
    "districts_not": "federal_district_not",
    "categories": "category",
    "categories_not": "category_not",
    "sentiments": "sentiment",
    "sentiments_not": "sentiment_not",
    "channels": "channel",
    "channels_not": "channel_not",
    "languages": "language",
    "languages_not": "language_not",
    "pipeline_statuses": "pipeline_status",
    "pipeline_statuses_not": "pipeline_status_not",
    "tags": "tag",
    "tags_not": "tag_not",
    "authors": "author",
    "authors_not": "author_not",
    "domains": "domain",
    "domains_not": "domain_not",
    "fake_labels": "fake_label",
    "fake_labels_not": "fake_label_not",
}


@dataclass
class ArticleFilters:
    sources: list[str] = field(default_factory=list)
    sources_not: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    source_types_not: list[str] = field(default_factory=list)
    source_statuses: list[str] = field(default_factory=list)
    source_statuses_not: list[str] = field(default_factory=list)
    official: bool | None = None
    regions: list[str] = field(default_factory=list)
    regions_not: list[str] = field(default_factory=list)
    region_codes: list[str] = field(default_factory=list)
    region_codes_not: list[str] = field(default_factory=list)
    districts: list[str] = field(default_factory=list)
    districts_not: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    categories_not: list[str] = field(default_factory=list)
    sentiments: list[str] = field(default_factory=list)
    sentiments_not: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    channels_not: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    languages_not: list[str] = field(default_factory=list)
    pipeline_statuses: list[str] = field(default_factory=list)
    pipeline_statuses_not: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    tags_not: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    authors_not: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    domains_not: list[str] = field(default_factory=list)
    fake_labels: list[str] = field(default_factory=list)
    fake_labels_not: list[str] = field(default_factory=list)
    weekdays: list[int] = field(default_factory=list)
    hours: list[int] = field(default_factory=list)
    url_contains: str | None = None
    date_on: str = "published"
    period: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
    text: str | None = None
    title_text: str | None = None
    body_text: str | None = None
    fuzzy: bool = False
    sentiment_min: float | None = None
    sentiment_max: float | None = None
    category_confidence_min: float | None = None
    category_confidence_max: float | None = None
    fake_score_min: float | None = None
    fake_score_max: float | None = None
    has_fulltext: bool | None = None
    text_len_min: int | None = None
    text_len_max: int | None = None
    min_cluster_sources: int | None = None
    limit: int = 50
    offset: int = 0

    # Names kept so older call sites (CLI, tests) still construct a filter.
    source_id: str | None = None
    source_type: str | None = None
    region_code: str | None = None
    federal_district: str | None = None
    category: str | None = None
    sentiment: str | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        _absorb(self.sources, self.source_id)
        _absorb(self.source_types, self.source_type)
        _absorb(self.region_codes, self.region_code)
        _absorb(self.districts, self.federal_district)
        _absorb(self.categories, self.category)
        _absorb(self.sentiments, self.sentiment)
        _absorb(self.languages, self.language)
        self.channels = [item for item in self.channels if item in CHANNELS]
        self.channels_not = [item for item in self.channels_not if item in CHANNELS]
        self.weekdays = [item for item in self.weekdays if 1 <= item <= 7]
        self.hours = [item for item in self.hours if 0 <= item <= 23]
        if self.date_on not in {"published", "collected"}:
            self.date_on = "published"
        self.source_id = None
        self.source_type = None
        self.region_code = None
        self.federal_district = None
        self.category = None
        self.sentiment = None
        self.language = None

    def to_query(self) -> dict:
        """Query-string keys shared by the API, saved searches and the page URL."""
        payload: dict = {}
        for attr, key in _LIST_FIELDS.items():
            values = getattr(self, attr)
            if values:
                payload[key] = list(values)
        if self.weekdays:
            payload["weekday"] = self.weekdays
        if self.hours:
            payload["hour"] = self.hours
        if self.official is True:
            payload["official"] = "yes"
        elif self.official is False:
            payload["official"] = "no"
        if self.url_contains:
            payload["url"] = self.url_contains
        if self.date_on != "published":
            payload["date_on"] = self.date_on
        if self.period:
            payload["period"] = self.period
        if self.date_from:
            payload["date_from"] = self.date_from.isoformat()
        if self.date_to:
            payload["date_to"] = self.date_to.isoformat()
        if self.text:
            payload["q"] = self.text
        if self.title_text:
            payload["title_q"] = self.title_text
        if self.body_text:
            payload["body_q"] = self.body_text
        if self.fuzzy:
            payload["fuzzy"] = "true"
        for key, value in (
            ("sentiment_min", self.sentiment_min),
            ("sentiment_max", self.sentiment_max),
            ("category_confidence_min", self.category_confidence_min),
            ("category_confidence_max", self.category_confidence_max),
            ("fake_score_min", self.fake_score_min),
            ("fake_score_max", self.fake_score_max),
            ("text_len_min", self.text_len_min),
            ("text_len_max", self.text_len_max),
            ("min_cluster_sources", self.min_cluster_sources),
        ):
            if value is not None:
                payload[key] = value
        if self.has_fulltext is True:
            payload["has_fulltext"] = "true"
        elif self.has_fulltext is False:
            payload["has_fulltext"] = "false"
        return payload


def parse_filters(raw, *, limit: int = 50, offset: int = 0) -> ArticleFilters:
    """Read the shared query keys from a dict or a Starlette multi-dict."""

    def many(key: str, *, commas: bool = True) -> list[str]:
        if hasattr(raw, "getlist"):
            values = list(raw.getlist(key))
        else:
            values = raw.get(key, [])
            if values is None or values == "":
                values = []
            elif not isinstance(values, (list, tuple)):
                values = [values]
        found: list[str] = []
        for value in values:
            pieces = str(value).split(",") if commas else [str(value)]
            for piece in pieces:
                piece = piece.strip()
                if piece:
                    found.append(piece)
        return found

    def scalar(key: str) -> str | None:
        if hasattr(raw, "getlist"):
            values = [item for item in raw.getlist(key) if str(item).strip()]
            value = values[-1] if values else None
        else:
            value = raw.get(key)
        if value is None:
            return None
        text_value = str(value).strip()
        return text_value or None

    def as_date(key: str) -> date | None:
        value = scalar(key)
        if not value:
            return None
        if isinstance(raw.get(key) if not hasattr(raw, "getlist") else value, date) and not isinstance(value, str):
            return value
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    def as_float(key: str) -> float | None:
        value = scalar(key)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def as_int(key: str) -> int | None:
        value = scalar(key)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def flag(key: str) -> bool | None:
        value = (scalar(key) or "").lower()
        if value in {"1", "true", "yes", "да"}:
            return True
        if value in {"0", "false", "no", "нет"}:
            return False
        return None

    integers = []
    for key in ("weekday",):
        integers.extend(int(item) for item in many(key) if item.lstrip("-").isdigit())
    hours = [int(item) for item in many("hour") if item.lstrip("-").isdigit()]
    return ArticleFilters(
        sources=many("source_id"),
        sources_not=many("source_id_not"),
        source_types=many("source_type"),
        source_types_not=many("source_type_not"),
        source_statuses=many("source_status"),
        source_statuses_not=many("source_status_not"),
        official=flag("official"),
        regions=many("region", commas=False),
        regions_not=many("region_not", commas=False),
        region_codes=many("region_code"),
        region_codes_not=many("region_code_not"),
        districts=many("federal_district", commas=False),
        districts_not=many("federal_district_not", commas=False),
        categories=many("category", commas=False),
        categories_not=many("category_not", commas=False),
        sentiments=many("sentiment"),
        sentiments_not=many("sentiment_not"),
        channels=many("channel"),
        channels_not=many("channel_not"),
        languages=many("language"),
        languages_not=many("language_not"),
        pipeline_statuses=many("pipeline_status"),
        pipeline_statuses_not=many("pipeline_status_not"),
        tags=many("tag", commas=False),
        tags_not=many("tag_not", commas=False),
        authors=many("author", commas=False),
        authors_not=many("author_not", commas=False),
        domains=many("domain"),
        domains_not=many("domain_not"),
        fake_labels=many("fake_label", commas=False),
        fake_labels_not=many("fake_label_not", commas=False),
        weekdays=integers,
        hours=hours,
        url_contains=scalar("url"),
        date_on=scalar("date_on") or "published",
        period=scalar("period"),
        date_from=as_date("date_from"),
        date_to=as_date("date_to"),
        text=scalar("q"),
        title_text=scalar("title_q"),
        body_text=scalar("body_q"),
        fuzzy=flag("fuzzy") is True,
        sentiment_min=as_float("sentiment_min"),
        sentiment_max=as_float("sentiment_max"),
        category_confidence_min=as_float("category_confidence_min"),
        category_confidence_max=as_float("category_confidence_max"),
        fake_score_min=as_float("fake_score_min"),
        fake_score_max=as_float("fake_score_max"),
        has_fulltext=flag("has_fulltext"),
        text_len_min=as_int("text_len_min"),
        text_len_max=as_int("text_len_max"),
        min_cluster_sources=as_int("min_cluster_sources"),
        limit=as_int("limit") or limit,
        offset=as_int("offset") or offset,
    )


def resolve_period(filters: ArticleFilters) -> ArticleFilters:
    if filters.time_from or filters.time_to:
        return filters
    if not filters.period or filters.period == "custom" or filters.date_from or filters.date_to:
        return filters
    if filters.period == "24h":
        filters.time_to = datetime.now(timezone.utc)
        filters.time_from = filters.time_to - timedelta(hours=24)
        return filters
    today = datetime.now(MOSCOW).date()
    spans = {"today": 0, "7d": 6, "30d": 29, "365d": 364}
    if filters.period in spans:
        filters.date_from = today - timedelta(days=spans[filters.period])
        filters.date_to = today
    elif filters.period == "month":
        filters.date_from = today.replace(day=1)
        filters.date_to = today
    return filters


def apply_article_filters(session: Session, stmt, filters: ArticleFilters):
    filters = resolve_period(filters)
    stmt = _categorical(stmt, Source.id, filters.sources, filters.sources_not)
    stmt = _categorical(stmt, Source.type, filters.source_types, filters.source_types_not)
    stmt = _categorical(stmt, Source.status, filters.source_statuses, filters.source_statuses_not)
    stmt = _categorical(stmt, Source.region, filters.regions, filters.regions_not)
    stmt = _categorical(stmt, Source.region_code, filters.region_codes, filters.region_codes_not)
    stmt = _categorical(stmt, Source.federal_district, filters.districts, filters.districts_not)
    stmt = _categorical(stmt, Article.category, filters.categories, filters.categories_not)
    stmt = _categorical(stmt, Article.sentiment, filters.sentiments, filters.sentiments_not)
    stmt = _categorical(stmt, Article.language, filters.languages, filters.languages_not)
    stmt = _categorical(stmt, Article.pipeline_status, filters.pipeline_statuses, filters.pipeline_statuses_not)
    stmt = _categorical(stmt, Article.domain, filters.domains, filters.domains_not)
    stmt = _categorical(stmt, Article.fake_label, filters.fake_labels, filters.fake_labels_not)
    if filters.official is True:
        stmt = stmt.where(Source.status.in_(OFFICIAL_STATUSES))
    elif filters.official is False:
        stmt = stmt.where(Source.status.notin_(OFFICIAL_STATUSES))
    stmt = _channels(stmt, filters.channels, filters.channels_not)
    stmt = _json_list(session, stmt, "tags", filters.tags, exact=True)
    stmt = _json_list(session, stmt, "tags", filters.tags_not, exact=True, negate=True)
    stmt = _json_list(session, stmt, "authors", filters.authors, exact=False)
    stmt = _json_list(session, stmt, "authors", filters.authors_not, exact=False, negate=True)
    if filters.sentiment_min is not None:
        stmt = stmt.where(Article.sentiment_score >= filters.sentiment_min)
    if filters.sentiment_max is not None:
        stmt = stmt.where(Article.sentiment_score <= filters.sentiment_max)
    if filters.category_confidence_min is not None:
        stmt = stmt.where(Article.category_confidence >= filters.category_confidence_min)
    if filters.category_confidence_max is not None:
        stmt = stmt.where(Article.category_confidence <= filters.category_confidence_max)
    if filters.fake_score_min is not None:
        stmt = stmt.where(Article.fake_score >= filters.fake_score_min)
    if filters.fake_score_max is not None:
        stmt = stmt.where(Article.fake_score <= filters.fake_score_max)
    length = func.coalesce(Article.text_length, func.length(Article.text))
    if filters.has_fulltext is True:
        stmt = stmt.where(length >= 500)
    elif filters.has_fulltext is False:
        stmt = stmt.where(length < 500)
    if filters.text_len_min is not None:
        stmt = stmt.where(length >= filters.text_len_min)
    if filters.text_len_max is not None:
        stmt = stmt.where(length <= filters.text_len_max)
    if filters.url_contains:
        needle = f"%{filters.url_contains.casefold()}%"
        if session.get_bind().dialect.name == "sqlite":
            stmt = stmt.where(func.py_lower(Article.canonical_url).like(needle))
        else:
            stmt = stmt.where(func.lower(Article.canonical_url).like(needle))
    stmt = _when(session, stmt, filters)
    stmt = _clock(session, stmt, filters)
    if filters.min_cluster_sources and filters.min_cluster_sources > 1:
        cluster = (
            select(Article.content_hash)
            .group_by(Article.content_hash)
            .having(func.count(func.distinct(Article.source_id)) >= filters.min_cluster_sources)
        )
        stmt = stmt.where(Article.content_hash.in_(cluster))
    fuzzy = filters.fuzzy and filters.text and session.get_bind().dialect.name == "postgresql"
    if fuzzy:
        stmt = stmt.where(text("(articles.title % :fuzzy_q OR articles.text % :fuzzy_q)").bindparams(fuzzy_q=filters.text))
    else:
        stmt = _text_query(session, stmt, filters.text, title=True, body=True, name="fts_q")
    stmt = _text_query(session, stmt, filters.title_text, title=True, body=False, name="fts_title")
    stmt = _text_query(session, stmt, filters.body_text, title=False, body=True, name="fts_body")
    return stmt


def facet_counts(session: Session, filters: ArticleFilters) -> dict[str, list[dict]]:
    """Counts for each value as if that facet's own include/exclude were not applied."""
    filters = resolve_period(filters)
    specs = [
        ("category", Article.category, None, "categories", "categories_not"),
        ("sentiment", Article.sentiment, None, "sentiments", "sentiments_not"),
        ("source_type", Source.type, None, "source_types", "source_types_not"),
        ("federal_district", Source.federal_district, None, "districts", "districts_not"),
        ("region_code", Source.region_code, Source.region, "region_codes", "region_codes_not"),
        ("source", Source.id, Source.name, "sources", "sources_not"),
        ("language", Article.language, None, "languages", "languages_not"),
        ("pipeline_status", Article.pipeline_status, None, "pipeline_statuses", "pipeline_statuses_not"),
        ("source_status", Source.status, None, "source_statuses", "source_statuses_not"),
        ("domain", Article.domain, None, "domains", "domains_not"),
        ("fake_label", Article.fake_label, None, "fake_labels", "fake_labels_not"),
    ]
    found = {}
    for name, column, label_column, include_attr, exclude_attr in specs:
        cleared = _cleared(filters, include_attr, exclude_attr)
        columns = [column, func.count()] if label_column is None else [column, label_column, func.count()]
        stmt = select(*columns).select_from(Article).join(Source, Article.source_id == Source.id)
        stmt = apply_article_filters(session, stmt, cleared)
        stmt = stmt.group_by(column) if label_column is None else stmt.group_by(column, label_column)
        include = set(getattr(filters, include_attr))
        exclude = set(getattr(filters, exclude_attr))
        rows = []
        for record in session.execute(stmt).all():
            value = record[0]
            if value is None:
                continue
            label = str(value if label_column is None else record[1] or value)
            count = int(record[-1])
            rows.append(
                {
                    "value": str(value),
                    "label": label,
                    "count": count,
                    "included": str(value) in include,
                    "excluded": str(value) in exclude,
                }
            )
        rows.sort(key=lambda item: (-item["count"], item["label"]))
        found[name] = rows
    found["channel"] = _channel_facet(session, filters)
    found["weekday"] = _clock_facet(session, filters, "weekday")
    found["tag"] = _tag_facet(session, filters)
    return found


def _channel_facet(session: Session, filters: ArticleFilters) -> list[dict]:
    cleared = _cleared(filters, "channels", "channels_not")
    stmt = (
        select(Article.content_origin, func.count())
        .select_from(Article)
        .join(Source, Article.source_id == Source.id)
    )
    stmt = apply_article_filters(session, stmt, cleared).group_by(Article.content_origin)
    counts = {channel: 0 for channel in CHANNELS}
    for origin, count in session.execute(stmt).all():
        channel = "rss" if origin in {None, "rss", "extracted"} else str(origin)
        if channel in counts:
            counts[channel] += int(count)
    labels = {"rss": "RSS", "telegram": "Telegram", "vk": "VK", "x": "X"}
    return [
        {
            "value": channel,
            "label": labels[channel],
            "count": counts[channel],
            "included": channel in filters.channels,
            "excluded": channel in filters.channels_not,
        }
        for channel in CHANNELS
        if counts[channel] or channel in filters.channels or channel in filters.channels_not
    ]


def _clock_facet(session: Session, filters: ArticleFilters, kind: str) -> list[dict]:
    cleared = _cleared(filters, "weekdays", "weekdays") if kind == "weekday" else filters
    dialect = session.get_bind().dialect.name
    expression = literal_column(_weekday_sql(dialect) if kind == "weekday" else _hour_sql(dialect))
    stmt = select(expression, func.count()).select_from(Article).join(Source, Article.source_id == Source.id)
    stmt = apply_article_filters(session, stmt, cleared).group_by(expression)
    counts = {int(value): int(count) for value, count in session.execute(stmt).all() if value is not None}
    if kind == "weekday":
        return [
            {
                "value": str(index),
                "label": WEEKDAYS[index - 1],
                "count": counts.get(index, 0),
                "included": index in filters.weekdays,
                "excluded": False,
            }
            for index in range(1, 8)
            if counts.get(index, 0) or index in filters.weekdays
        ]
    return []


def _tag_facet(session: Session, filters: ArticleFilters) -> list[dict]:
    cleared = _cleared(filters, "tags", "tags_not")
    stmt = select(Article.tags).select_from(Article).join(Source, Article.source_id == Source.id)
    stmt = apply_article_filters(session, stmt, cleared).limit(20000)
    counts: dict[str, int] = {}
    for (tags,) in session.execute(stmt).all():
        for tag in tags or []:
            counts[str(tag)] = counts.get(str(tag), 0) + 1
    include = set(filters.tags)
    exclude = set(filters.tags_not)
    rows = [
        {
            "value": tag,
            "label": tag,
            "count": count,
            "included": tag in include,
            "excluded": tag in exclude,
        }
        for tag, count in counts.items()
    ]
    rows.sort(key=lambda item: (-item["count"], item["label"]))
    return rows[:40]


def _cleared(filters: ArticleFilters, include_attr: str, exclude_attr: str) -> ArticleFilters:
    copied = ArticleFilters(**{item.name: getattr(filters, item.name) for item in fields(filters)})
    setattr(copied, include_attr, [] if isinstance(getattr(filters, include_attr), list) else None)
    if exclude_attr != include_attr:
        setattr(copied, exclude_attr, [])
    copied.limit = filters.limit
    copied.offset = 0
    return copied


def _categorical(stmt, column, include: list[str], exclude: list[str]):
    if include:
        stmt = stmt.where(column.in_(include))
    if exclude:
        stmt = stmt.where(or_(column.is_(None), column.notin_(exclude)))
    return stmt


def _channels(stmt, include: list[str], exclude: list[str]):
    if include:
        stmt = stmt.where(text("(" + " OR ".join(_channel_sql(item) for item in include) + ")"))
    if exclude:
        stmt = stmt.where(text("NOT (" + " OR ".join(_channel_sql(item) for item in exclude) + ")"))
    return stmt


def _channel_sql(channel: str) -> str:
    if channel == "rss":
        return "articles.content_origin IN ('rss', 'extracted')"
    return f"articles.content_origin = '{channel}'"


def _json_list(session: Session, stmt, column: str, values: list[str], *, exact: bool, negate: bool = False):
    if not values:
        return stmt
    dialect = session.get_bind().dialect.name
    parts = []
    params = {}
    for index, value in enumerate(values):
        key = f"{column}_{'not' if negate else 'yes'}_{index}"
        folded = value.casefold()
        params[key] = folded if exact else f"%{folded}%"
        compare = "=" if exact else "LIKE"
        if dialect == "sqlite":
            parts.append(
                f"EXISTS (SELECT 1 FROM json_each(articles.{column}) AS item "
                f"WHERE py_lower(item.value) {compare} :{key})"
            )
        else:
            parts.append(
                f"EXISTS (SELECT 1 FROM json_array_elements_text(articles.{column}::json) AS item(value) "
                f"WHERE lower(item.value) {compare} :{key})"
            )
    clause = text(("NOT (" if negate else "(") + " OR ".join(parts) + ")").bindparams(**params)
    return stmt.where(clause)


def _when(session: Session, stmt, filters: ArticleFilters):
    column = Article.fetched_at if filters.date_on == "collected" else Article.published_at
    if filters.time_from is not None:
        stmt = stmt.where(column >= filters.time_from)
    if filters.time_to is not None:
        stmt = stmt.where(column <= filters.time_to)
    if filters.time_from is None and filters.time_to is None:
        if filters.date_from is not None:
            stmt = stmt.where(column >= _day_start(filters.date_from))
        if filters.date_to is not None:
            stmt = stmt.where(column < _day_start(filters.date_to + timedelta(days=1)))
    return stmt


def _clock(session: Session, stmt, filters: ArticleFilters):
    if not filters.weekdays and not filters.hours:
        return stmt
    dialect = session.get_bind().dialect.name
    if filters.weekdays:
        numbers = ", ".join(str(item) for item in filters.weekdays)
        stmt = stmt.where(text(f"({_weekday_sql(dialect)}) IN ({numbers})"))
    if filters.hours:
        numbers = ", ".join(str(item) for item in filters.hours)
        stmt = stmt.where(text(f"({_hour_sql(dialect)}) IN ({numbers})"))
    return stmt


def _weekday_sql(dialect: str) -> str:
    if dialect == "sqlite":
        return "((cast(strftime('%w', datetime(articles.published_at, '+3 hours')) as integer) + 6) % 7) + 1"
    return "EXTRACT(ISODOW FROM articles.published_at + interval '3 hours')"


def _hour_sql(dialect: str) -> str:
    if dialect == "sqlite":
        return "cast(strftime('%H', datetime(articles.published_at, '+3 hours')) as integer)"
    return "EXTRACT(HOUR FROM articles.published_at + interval '3 hours')"


def _text_query(session: Session, stmt, query: str | None, *, title: bool, body: bool, name: str):
    needle = (query or "").strip()
    if not needle:
        return stmt
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql" and not (title and body and False):
        if title and body:
            sql = f"articles.search_vector @@ websearch_to_tsquery('russian', :{name})"
        elif title:
            sql = f"to_tsvector('russian', coalesce(articles.title, '')) @@ websearch_to_tsquery('russian', :{name})"
        else:
            sql = f"to_tsvector('russian', coalesce(articles.text, '')) @@ websearch_to_tsquery('russian', :{name})"
        return stmt.where(text(sql).bindparams(**{name: needle}))
    columns = []
    if title:
        columns.append(Article.title)
    if body:
        columns.append(Article.text)
    return _sqlite_text_filter(stmt, needle, columns)


def _sqlite_text_filter(stmt, query: str, columns):
    groups = _parse_websearch(query)
    clauses = []
    for group in groups:
        parts = []
        for phrase, negated in group:
            folded = f"%{phrase.casefold()}%"
            match = or_(*(func.py_lower(column).like(folded) for column in columns))
            parts.append(~match if negated else match)
        if parts:
            clause = parts[0]
            for part in parts[1:]:
                clause = clause & part
            clauses.append(clause)
    if not clauses:
        return stmt
    combined = clauses[0]
    for clause in clauses[1:]:
        combined = combined | clause
    return stmt.where(combined)


def _parse_websearch(query: str) -> list[list[tuple[str, bool]]]:
    groups: list[list[tuple[str, bool]]] = [[]]
    for token in re.findall(r'"[^"]+"|\S+', query):
        if token == "OR":
            groups.append([])
            continue
        negated = token.startswith("-") and len(token) > 1
        word = (token[1:] if negated else token).strip('"').strip()
        if word:
            groups[-1].append((word, negated))
    return [group for group in groups if group]


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=MOSCOW).astimezone(timezone.utc)


def _absorb(target: list[str], extra: str | None) -> None:
    if extra and extra not in target:
        target.append(extra)
