from __future__ import annotations

import logging

from fast_parser.models import Article
from fast_parser.pipeline.base import ArticleProcessor, ArticleView, ProcessorResult

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, processors: list[ArticleProcessor]) -> None:
        self.processors = list(processors)

    def apply(self, article: Article) -> bool:
        view = ArticleView(
            source_id=article.source_id,
            url=article.canonical_url,
            title=article.title,
            text=article.text or "",
            summary=article.summary,
            authors=tuple(article.authors or []),
            published_at=article.published_at,
            language=article.language,
            rss_categories=_rss_categories(article),
        )
        return self._apply_view(article, view)

    def apply_many(self, articles: list[Article]) -> int:
        if not articles:
            return 0
        views = [
            ArticleView(
                source_id=article.source_id,
                url=article.canonical_url,
                title=article.title,
                text=article.text or "",
                summary=article.summary,
                authors=tuple(article.authors or []),
                published_at=article.published_at,
                language=article.language,
                rss_categories=_rss_categories(article),
            )
            for article in articles
        ]
        columns: list[list[ProcessorResult]] = []
        for processor in self.processors:
            try:
                if hasattr(processor, "process_many"):
                    columns.append(processor.process_many(views))
                else:
                    columns.append([processor.process(view) for view in views])
            except Exception:
                log.exception("processor %s failed", processor.name)
                columns.append([ProcessorResult(handled=False) for _ in views])
        done = 0
        for index, article in enumerate(articles):
            for column in columns:
                _write(article, column[index])
            if article.category and article.sentiment:
                article.pipeline_status = "processed"
                done += 1
        return done

    def _apply_view(self, article: Article, view: ArticleView) -> bool:
        for processor in self.processors:
            try:
                result = processor.process(view)
            except Exception:
                log.exception("processor %s failed on %s", processor.name, article.canonical_url)
                continue
            _write(article, result)
        if article.category and article.sentiment:
            article.pipeline_status = "processed"
            return True
        return False


def _write(article: Article, result: ProcessorResult) -> bool:
    if not result.handled:
        return False
    if result.category is not None:
        article.category = result.category
    if result.sentiment is not None:
        article.sentiment = result.sentiment
    if result.sentiment_score is not None:
        article.sentiment_score = result.sentiment_score
    if result.fake_score is not None:
        article.fake_score = result.fake_score
    if result.category_confidence is not None:
        article.category_confidence = result.category_confidence
        extra = dict(article.extra or {})
        extra["category_confidence"] = result.category_confidence
        article.extra = extra
    if result.category is not None:
        from sqlalchemy.orm import object_session

        from fast_parser.storage import category_id_for

        bound = object_session(article)
        if bound is not None:
            article.category_id = category_id_for(bound, result.category)
    return True


def _rss_categories(article: Article) -> tuple[str, ...]:
    raw = (article.extra or {}).get("categories") or []
    if isinstance(raw, str):
        return (raw,)
    return tuple(str(item) for item in raw if item)
