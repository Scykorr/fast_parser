from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

ALLOWED_SENTIMENTS = frozenset({"positive", "neutral", "negative"})


@dataclass(frozen=True)
class ArticleView:
    source_id: str
    url: str
    title: str
    text: str
    summary: str | None
    authors: tuple[str, ...]
    published_at: datetime | None
    language: str | None


@dataclass
class ProcessorResult:
    """Fields left as None are not written. `handled=False` means the stub did nothing."""

    handled: bool = False
    category: str | None = None
    sentiment: str | None = None
    sentiment_score: float | None = None
    fake_score: float | None = None

    def __post_init__(self) -> None:
        if self.sentiment is not None and self.sentiment not in ALLOWED_SENTIMENTS:
            raise ValueError(
                "sentiment must be positive, neutral or negative, "
                f"got {self.sentiment!r}"
            )


class ArticleProcessor(Protocol):
    name: str

    def process(self, article: ArticleView) -> ProcessorResult:
        """Enrich one article. Must not perform network I/O in stage 1 stubs."""
