"""Category and sentiment processors.

Keywords and RSS rubrics always run. Embeddings and the sentiment model run
only when the optional ``ml`` extra is installed; otherwise those steps no-op.
"""

from __future__ import annotations

import logging
import os

from fast_parser.config import AppSettings
from fast_parser.pipeline.base import ArticleView, ProcessorResult
from fast_parser.pipeline.keywords import lexical_scores
from fast_parser.pipeline.llm import LlmFallback
from fast_parser.pipeline.ml_models import category_encoder, ml_available, sentiment_model
from fast_parser.pipeline.taxonomy import CategorySpec, load_taxonomy

log = logging.getLogger(__name__)

_LEXICAL_FLOOR = 0.34


class CategoryProcessor:
    name = "category"

    def __init__(self, settings: AppSettings, taxonomy: list[CategorySpec] | None = None) -> None:
        self.settings = settings
        self.taxonomy = taxonomy if taxonomy is not None else load_taxonomy(settings.categories_path)
        self._encoder = None
        self._encoder_failed = False
        self._llm = LlmFallback(
            settings.pipeline.llm,
            [item.title for item in self.taxonomy],
        )

    def process(self, article: ArticleView) -> ProcessorResult:
        return self.process_many([article])[0]

    def process_many(self, articles: list[ArticleView]) -> list[ProcessorResult]:
        lexical = [
            lexical_scores(
                title=article.title,
                text=article.text,
                rss_categories=article.rss_categories,
                taxonomy=self.taxonomy,
            )
            for article in articles
        ]
        embedded = self._embed(articles)
        results: list[ProcessorResult] = []
        for article, words, vectors in zip(articles, lexical, embedded):
            blended = _blend(words, vectors)
            title, score = _best(blended)
            if score < self.settings.pipeline.min_confidence:
                guessed, _, llm_score = self._llm.classify(article.title, article.text)
                if guessed and llm_score >= score:
                    title, score = guessed, llm_score
            if title is None or score < _LEXICAL_FLOOR:
                results.append(ProcessorResult(handled=False))
                continue
            results.append(
                ProcessorResult(handled=True, category=title, category_confidence=round(score, 4))
            )
        return results

    def _embed(self, articles: list[ArticleView]) -> list[dict[str, float] | None]:
        if _tests_skip_models() or not self.settings.pipeline.category.use_embeddings or not ml_available():
            return [None] * len(articles)
        if self._encoder_failed:
            return [None] * len(articles)
        try:
            encoder = category_encoder(self.settings.pipeline.category.model)
            texts = [_snippet(article) for article in articles]
            prototypes = [(item.title, item.description) for item in self.taxonomy]
            return encoder.similarities(texts, prototypes)
        except Exception:
            log.warning("category embeddings are unavailable, keywords only", exc_info=True)
            self._encoder_failed = True
            return [None] * len(articles)


class SentimentProcessor:
    name = "sentiment"

    def __init__(self, settings: AppSettings, taxonomy: list[CategorySpec] | None = None) -> None:
        self.settings = settings
        self._model = None
        self._failed = False
        titles = [item.title for item in taxonomy] if taxonomy is not None else []
        if not titles and settings.categories_path.exists():
            titles = [item.title for item in load_taxonomy(settings.categories_path)]
        self._llm = LlmFallback(settings.pipeline.llm, titles)

    def process(self, article: ArticleView) -> ProcessorResult:
        return self.process_many([article])[0]

    def process_many(self, articles: list[ArticleView]) -> list[ProcessorResult]:
        if not articles:
            return []
        if _tests_skip_models() or not ml_available():
            return [ProcessorResult(handled=False) for _ in articles]
        if self._failed:
            return [ProcessorResult(handled=False) for _ in articles]
        try:
            predicted = sentiment_model(self.settings.pipeline.sentiment.model).predict(
                [_snippet(article) for article in articles]
            )
        except Exception:
            log.warning("sentiment model failed, leaving articles pending", exc_info=True)
            self._failed = True
            return [ProcessorResult(handled=False) for _ in articles]
        results: list[ProcessorResult] = []
        threshold = self.settings.pipeline.min_confidence
        for article, (label, score) in zip(articles, predicted):
            if score < threshold:
                _, llm_label, llm_score = self._llm.classify(article.title, article.text)
                if llm_label and llm_score >= score:
                    label, score = llm_label, llm_score
            results.append(
                ProcessorResult(handled=True, sentiment=label, sentiment_score=round(score, 4))
            )
        return results


def _blend(lexical: dict[str, float], embedded: dict[str, float] | None) -> dict[str, float]:
    if not embedded:
        return lexical
    keys = set(lexical) | set(embedded)
    return {key: 0.45 * lexical.get(key, 0.0) + 0.55 * embedded.get(key, 0.0) for key in keys}


def _best(scores: dict[str, float]) -> tuple[str | None, float]:
    if not scores:
        return None, 0.0
    title, score = max(scores.items(), key=lambda item: item[1])
    if score <= 0:
        return None, 0.0
    return title, score


def _tests_skip_models() -> bool:
    # The default suite stays offline. The slow test calls the model class directly.
    if os.environ.get("FAST_PARSER_ML_IN_TESTS") == "1":
        return False
    return "PYTEST_CURRENT_TEST" in os.environ


def _snippet(article: ArticleView) -> str:
    body = (article.text or article.summary or "").strip()
    return f"{article.title}. {body}"[:1500]
