from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(TypeDecorator):
    """Store UTC wall time and always read it back as an aware UTC datetime."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(64))
    site: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    telegram_username: Mapped[str | None] = mapped_column(String(128))
    telegram_title: Mapped[str | None] = mapped_column(String(255))
    telegram_check: Mapped[str | None] = mapped_column(String(32))
    telegram_meta: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    feeds: Mapped[list["Feed"]] = relationship(back_populates="source")
    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Feed(Base):
    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    title: Mapped[str | None] = mapped_column(String(512))
    fulltext_hint: Mapped[str | None] = mapped_column(String(64))
    check_status: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    http_code: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict | None] = mapped_column(JSON)

    source: Mapped["Source"] = relationship(back_populates="feeds")
    state: Mapped["FeedState | None"] = relationship(back_populates="feed", uselist=False)
    articles: Mapped[list["Article"]] = relationship(back_populates="feed")
    fetch_logs: Mapped[list["FetchLog"]] = relationship(back_populates="feed")


class FeedState(Base):
    """Per-feed catch-up cursor: validators for conditional GET and the newest item seen."""

    __tablename__ = "feed_states"

    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id"), primary_key=True)
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    newest_published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    newest_guid: Mapped[str | None] = mapped_column(String(1024))
    newest_url: Mapped[str | None] = mapped_column(String(2048))
    items_seen: Mapped[int] = mapped_column(Integer, default=0)

    feed: Mapped["Feed"] = relationship(back_populates="state")


class FetchLog(Base):
    __tablename__ = "fetch_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(32))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    items_fetched: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, default=0)

    feed: Mapped["Feed"] = relationship(back_populates="fetch_logs")


class Article(Base):
    """Stored article. Derived NLP columns stay null until a later stage fills them."""

    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("canonical_url", name="uq_articles_canonical_url"),
        UniqueConstraint("source_id", "content_hash", name="uq_articles_source_content"),
        Index("ix_articles_feed_guid", "feed_id", "guid"),
        Index("ix_articles_published_at", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    feed_id: Mapped[int | None] = mapped_column(ForeignKey("feeds.id"), index=True)
    guid: Mapped[str | None] = mapped_column(String(1024))
    url: Mapped[str] = mapped_column(String(2048))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[list | None] = mapped_column(JSON)
    language: Mapped[str | None] = mapped_column(String(16), default="ru")
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    category: Mapped[str | None] = mapped_column(String(128))
    sentiment: Mapped[str | None] = mapped_column(String(16))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    fake_score: Mapped[float | None] = mapped_column(Float)
    content_origin: Mapped[str] = mapped_column(String(32), default="rss")
    pipeline_status: Mapped[str] = mapped_column(String(32), default="pending")
    extra: Mapped[dict | None] = mapped_column(JSON)

    source: Mapped["Source"] = relationship(back_populates="articles")
    feed: Mapped["Feed | None"] = relationship(back_populates="articles")

    def to_export_dict(self, source_name: str | None = None) -> dict:
        """Clean text plus the metadata a later RAG export will send as-is."""
        published = self.published_at.isoformat() if self.published_at else None
        return {
            "source": self.source_id,
            "source_name": source_name,
            "url": self.canonical_url,
            "published_at": published,
            "authors": list(self.authors or []),
            "title": self.title,
            "text": self.text,
            "category": self.category,
            "sentiment": self.sentiment,
            "fake_score": self.fake_score,
        }
