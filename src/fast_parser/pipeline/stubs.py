from __future__ import annotations

from fast_parser.config import AppSettings
from fast_parser.pipeline.base import ArticleView, ProcessorResult
from fast_parser.pipeline.classify import CategoryProcessor, SentimentProcessor


class CategoryStub:
    """Stage 2: rubert-tiny2 (or a larger Russian classifier) plus an LLM fallback."""

    name = "category"

    def process(self, article: ArticleView) -> ProcessorResult:
        return ProcessorResult(handled=False)


class SentimentStub:
    """Stage 2: positive / neutral / negative. The column stays null until then."""

    name = "sentiment"

    def process(self, article: ArticleView) -> ProcessorResult:
        return ProcessorResult(handled=False)


class FakeScoreStub:
    """Stage 4: source reliability, cross-source clustering, fact-check sites."""

    name = "fake_score"

    def process(self, article: ArticleView) -> ProcessorResult:
        return ProcessorResult(handled=False)


PROCESSOR_REGISTRY: dict[str, type] = {
    "category": CategoryStub,
    "sentiment": SentimentStub,
    "fake_score": FakeScoreStub,
}


def build_processors(names: list[str], settings: AppSettings | None = None) -> list:
    if settings is None:
        from fast_parser.config import load_settings

        settings = load_settings()
    factories = {
        "category": lambda: CategoryProcessor(settings),
        "sentiment": lambda: SentimentProcessor(settings),
        "fake_score": FakeScoreStub,
    }
    processors = []
    for name in names:
        try:
            factory = factories[name]
        except KeyError as exc:
            known = ", ".join(sorted(factories))
            raise KeyError(f"Неизвестный процессор {name!r}. Доступны: {known}") from exc
        processors.append(factory())
    return processors
