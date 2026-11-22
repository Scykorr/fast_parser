from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ArticleListItem(BaseModel):
    id: int
    source_id: str
    source_name: str
    source_type: str
    region: str | None
    region_code: str | None
    federal_district: str | None
    title: str
    summary: str | None
    url: str
    published_at: datetime | None
    category: str | None
    sentiment: str | None
    fake_score: float | None


class ArticleDetail(ArticleListItem):
    text: str
    authors: list[str]
    sentiment_score: float | None
    content_origin: str


class ArticlePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ArticleListItem]


class FeedStatus(BaseModel):
    url: str
    title: str | None
    enabled: bool
    check_status: str | None
    last_status: str | None
    last_error: str | None
    last_success_at: datetime | None
    last_http_status: int | None
    consecutive_failures: int = 0


class SourceStatus(BaseModel):
    id: str
    name: str
    type: str
    site: str
    status: str
    region: str | None
    region_code: str | None
    federal_district: str | None
    telegram_username: str | None
    feeds: list[FeedStatus]


class RegionCoverage(BaseModel):
    region_code: str
    region: str | None
    federal_district: str | None
    sources: int
    rss_enabled: int
    telegram: int
    articles: int
    covered: bool


class RegionReport(BaseModel):
    subjects_total: int
    subjects_covered: int
    items: list[RegionCoverage]


class NamedCount(BaseModel):
    key: str
    label: str
    count: int


class StatsReport(BaseModel):
    articles: int
    sources: int
    feeds_enabled: int
    per_day: list[NamedCount]
    per_source: list[NamedCount]
    per_region: list[NamedCount]


class CollectResult(BaseModel):
    feeds_total: int
    feeds_ok: int
    feeds_not_modified: int
    feeds_empty: int
    feeds_error: int
    items_fetched: int
    items_new: int
    items_duplicate: int
    items_updated: int
    errors: list[str] = Field(default_factory=list)
