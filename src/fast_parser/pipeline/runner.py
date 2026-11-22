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
        )
        handled = False
        for processor in self.processors:
            try:
                result = processor.process(view)
            except Exception:
                log.exception("processor %s failed on %s", processor.name, article.canonical_url)
                continue
            if _write(article, result):
                handled = True
        if handled:
            article.pipeline_status = "processed"
        return handled


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
    return True
