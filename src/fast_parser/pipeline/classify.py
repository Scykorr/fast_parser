"""Category and sentiment processors.

Keywords and RSS rubrics always run. Embeddings and the sentiment model run
only when the optional ``ml`` extra is installed; otherwise those steps no-op.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fast_parser.config import AppSettings
from fast_parser.pipeline.base import ArticleView, ProcessorResult
from fast_parser.pipeline.head_features import category_feature, head_text, sentiment_feature
from fast_parser.pipeline.keywords import lexical_scores
from fast_parser.pipeline.linear_head import LinearHead
from fast_parser.pipeline.llm import LlmFallback
from fast_parser.pipeline.ml_models import category_encoder, ml_available, sentiment_model
from fast_parser.pipeline.taxonomy import CategorySpec, load_taxonomy

_CATEGORY_HEAD = Path(__file__).resolve().parent / "data" / "category_head.json"
_SENTIMENT_HEAD = Path(__file__).resolve().parent / "data" / "sentiment_head.json"

log = logging.getLogger(__name__)

_LEXICAL_FLOOR = 0.5
_EMBED_MARGIN = 0.045
_EMBED_MIN = 0.38

_HARM = (
    "погиб", "пострад", "уби", "ранен", "пожар", "дтп", "взрыв", "авари",
    "атак", "дрон", "задержа", "осужд", "приговор", "штраф", "мошен",
    "жертв", "покончил", "сбил", "пропал", "долг", "не выпла", "теракт",
    "террор", "преступн", "уголовн",
)
_GOOD = (
    "открыл", "открыли", "побед", "выигр", "награ", "поздрав", "юбиле",
    "спас", "рекорд", "праздн", "выделил", "восстанов", "победил",
    "заняли перв", "лучший", "списан", "вышел", "бенефис", "отметят",
    "контроль над",
)


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
        headed = self._headed(articles, lexical)
        embedded = [None] * len(articles) if headed is not None else self._embed(articles)
        results: list[ProcessorResult] = []
        for index, article in enumerate(articles):
            if headed is not None:
                title, score = headed[index]
            else:
                title, score = _choose_category(lexical[index], embedded[index])
            if score < self.settings.pipeline.min_confidence:
                guessed, _, llm_score = self._llm.classify(article.title, article.text)
                if guessed and llm_score >= score:
                    title, score = guessed, llm_score
            if title is None:
                results.append(ProcessorResult(handled=False))
                continue
            if headed is None and score < _LEXICAL_FLOOR and embedded[index] is None:
                results.append(ProcessorResult(handled=False))
                continue
            results.append(
                ProcessorResult(handled=True, category=title, category_confidence=round(score, 4))
            )
        return results

    def _headed(self, articles: list[ArticleView], lexical: list[dict[str, float]]) -> list[tuple[str, float]] | None:
        if (
            _tests_skip_models()
            or not self.settings.pipeline.category.use_embeddings
            or not self.settings.pipeline.category.use_head
            or not ml_available()
            or not _CATEGORY_HEAD.exists()
        ):
            return None
        try:
            head = LinearHead.load(_CATEGORY_HEAD)
            encoder = category_encoder(self.settings.pipeline.category.model)
            texts = [head_text(article.title, article.text) for article in articles]
            vectors = encoder.embed(texts)
            prototypes = encoder.similarities(
                texts, [(item.title, item.description) for item in self.taxonomy]
            )
            features = [
                category_feature(vector, proto, words, head.labels)
                for vector, proto, words in zip(vectors, prototypes, lexical)
            ]
            return head.predict(features)
        except Exception:
            log.warning("category head is unavailable, using keywords", exc_info=True)
            return None

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
        headed = self._headed(articles)
        try:
            if headed is None:
                predicted = sentiment_model(self.settings.pipeline.sentiment.model).predict(
                    [_snippet(article) for article in articles]
                )
            else:
                predicted = headed
        except Exception:
            log.warning("sentiment model failed, leaving articles pending", exc_info=True)
            self._failed = True
            return [ProcessorResult(handled=False) for _ in articles]
        results: list[ProcessorResult] = []
        threshold = self.settings.pipeline.min_confidence
        for article, (label, score) in zip(articles, predicted):
            if headed is None:
                label, score = _calibrate_news_tone(article.title, article.text, label, score)
            uncertain = score < threshold and (headed is not None or label == "neutral")
            if uncertain:
                _, llm_label, llm_score = self._llm.classify(article.title, article.text)
                if llm_label and llm_score >= score:
                    label, score = llm_label, llm_score
            results.append(
                ProcessorResult(handled=True, sentiment=label, sentiment_score=round(score, 4))
            )
        return results

    def _headed(self, articles: list[ArticleView]) -> list[tuple[str, float]] | None:
        if (
            _tests_skip_models()
            or not self.settings.pipeline.sentiment.use_head
            or not ml_available()
            or not _SENTIMENT_HEAD.exists()
        ):
            return None
        try:
            head = LinearHead.load(_SENTIMENT_HEAD)
            encoder = category_encoder(self.settings.pipeline.category.model)
            vectors = encoder.embed([head_text(article.title, article.text) for article in articles])
            return head.predict([sentiment_feature(vector) for vector in vectors])
        except Exception:
            log.warning("sentiment head is unavailable, using the sentiment model", exc_info=True)
            return None


def _choose_category(
    lexical: dict[str, float],
    embedded: dict[str, float] | None,
) -> tuple[str | None, float]:
    lexical_title, lexical_score = _best(lexical)
    if lexical_score >= _LEXICAL_FLOOR and lexical_title:
        return lexical_title, lexical_score
    if embedded:
        ranked = sorted(embedded.items(), key=lambda item: item[1], reverse=True)
        top_title, top_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if top_score >= _EMBED_MIN and top_score - second >= _EMBED_MARGIN:
            return top_title, top_score
    return None, lexical_score


def _best(scores: dict[str, float]) -> tuple[str | None, float]:
    if not scores:
        return None, 0.0
    title, score = max(scores.items(), key=lambda item: item[1])
    if score <= 0:
        return None, 0.0
    return title, score


def _calibrate_news_tone(title: str, text: str, label: str, score: float) -> tuple[str, float]:
    """A news wire is often scored neutral even when the headline is a loss or a celebration."""
    headline = title.casefold()
    lead = f"{headline} {(text or '')[:180].casefold()}"
    harm = _has_any(_strip_fire_crews(headline), _HARM) or _has_any(_strip_fire_crews(lead), _HARM)
    good = _has_any(headline, _GOOD)
    if harm and not good and label != "negative":
        return "negative", max(score, 0.7)
    if good and not harm and label == "neutral":
        return "positive", max(score, 0.66)
    return label, score


def _strip_fire_crews(text: str) -> str:
    return re.sub(r"пожарн\w*", " ", text)


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _tests_skip_models() -> bool:
    # The default suite stays offline. The slow test calls the model class directly.
    if os.environ.get("FAST_PARSER_ML_IN_TESTS") == "1":
        return False
    return "PYTEST_CURRENT_TEST" in os.environ


def _snippet(article: ArticleView) -> str:
    body = (article.text or article.summary or "").strip()
    return f"{article.title}. {body}"[:1500]
