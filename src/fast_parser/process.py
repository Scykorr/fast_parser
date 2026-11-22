"""Backfill category and sentiment for articles that are still incomplete."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select

from fast_parser.config import AppSettings
from fast_parser.models import Article
from fast_parser.pipeline.runner import Pipeline
from fast_parser.pipeline.stubs import build_processors
from fast_parser.service import open_session


@dataclass
class ProcessStats:
    seen: int = 0
    with_category: int = 0
    with_sentiment: int = 0
    completed: int = 0


def process_pending(
    settings: AppSettings,
    *,
    limit: int | None = None,
    all_articles: bool = False,
) -> ProcessStats:
    session = open_session(settings)
    try:
        stmt = select(Article).order_by(Article.id)
        if not all_articles:
            stmt = stmt.where(or_(Article.category.is_(None), Article.sentiment.is_(None)))
        if limit is not None:
            stmt = stmt.limit(limit)
        articles = list(session.scalars(stmt))
        pipeline = Pipeline(build_processors(settings.pipeline.processors, settings))
        batch_size = max(1, settings.pipeline.batch_size)
        stats = ProcessStats()
        for start in range(0, len(articles), batch_size):
            chunk = articles[start : start + batch_size]
            if all_articles:
                for article in chunk:
                    article.category = None
                    article.sentiment = None
                    article.sentiment_score = None
                    article.pipeline_status = "pending"
            pipeline.apply_many(chunk)
            session.commit()
            stats.seen += len(chunk)
            stats.with_category += sum(1 for article in chunk if article.category)
            stats.with_sentiment += sum(1 for article in chunk if article.sentiment)
            stats.completed += sum(1 for article in chunk if article.pipeline_status == "processed")
        return stats
    finally:
        session.close()
