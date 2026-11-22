"""Score category and sentiment predictions against a hand-labelled jsonl set."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from fast_parser.config import AppSettings
from fast_parser.pipeline.base import ArticleView
from fast_parser.pipeline.classify import CategoryProcessor, SentimentProcessor
from fast_parser.pipeline.ml_models import ml_available
from fast_parser.pipeline.taxonomy import load_taxonomy


@dataclass
class ClassMetrics:
    name: str
    labelled: int
    covered: int
    accuracy: float
    macro_f1: float
    distribution: dict[str, int] = field(default_factory=dict)


def load_labels(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    if not rows:
        raise ValueError(f"Пустой набор разметки: {path}")
    return rows


def score_dataset(settings: AppSettings, rows: list[dict], *, use_embeddings: bool) -> tuple[ClassMetrics, ClassMetrics | None]:
    views = [_view(row) for row in rows]
    taxonomy = load_taxonomy(settings.categories_path)
    saved = settings.pipeline.category.use_embeddings
    settings.pipeline.category.use_embeddings = use_embeddings and ml_available()
    try:
        categories = CategoryProcessor(settings, taxonomy).process_many(views)
    finally:
        settings.pipeline.category.use_embeddings = saved
    gold_cat = [row["category"] for row in rows]
    pred_cat = [item.category for item in categories]
    labels = [item.title for item in taxonomy]
    category_metrics = _metrics("category", gold_cat, pred_cat, labels)
    sentiment_metrics = None
    if ml_available() and use_embeddings:
        sentiments = SentimentProcessor(settings, taxonomy).process_many(views)
        sentiment_metrics = _metrics(
            "sentiment",
            [row["sentiment"] for row in rows],
            [item.sentiment for item in sentiments],
            ["positive", "neutral", "negative"],
        )
    return category_metrics, sentiment_metrics


def _view(row: dict) -> ArticleView:
    rss = row.get("rss") or []
    return ArticleView(
        source_id=row.get("source_id") or "eval",
        url=row.get("url") or "https://eval.local/item",
        title=row.get("title") or "",
        text=row.get("text") or "",
        summary=None,
        authors=(),
        published_at=None,
        language="ru",
        rss_categories=tuple(rss),
    )


def _metrics(name: str, gold: list[str], pred: list[str | None], labels: list[str]) -> ClassMetrics:
    covered = sum(1 for item in pred if item)
    correct = sum(1 for left, right in zip(gold, pred) if left == right)
    f1s = []
    for label in labels:
        tp = sum(1 for left, right in zip(gold, pred) if left == label and right == label)
        fp = sum(1 for left, right in zip(gold, pred) if left != label and right == label)
        fn = sum(1 for left, right in zip(gold, pred) if left == label and right != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
    return ClassMetrics(
        name=name,
        labelled=len(gold),
        covered=covered,
        accuracy=correct / len(gold) if gold else 0.0,
        macro_f1=sum(f1s) / len(f1s) if f1s else 0.0,
        distribution=dict(Counter(item or "—" for item in pred)),
    )


def format_report(keyword: ClassMetrics, ml_category: ClassMetrics | None, sentiment: ClassMetrics | None) -> str:
    lines = [
        "Оценка на ручной выборке",
        f"{'режим':<22}{'покрытие':>10}{'accuracy':>10}{'macro-F1':>10}",
        f"{'рубрики, словарь':<22}{_pct(keyword.covered, keyword.labelled):>10}{_pct_num(keyword.accuracy):>10}{_pct_num(keyword.macro_f1):>10}",
    ]
    if ml_category is not None:
        lines.append(
            f"{'рубрики, словарь+модель':<22}{_pct(ml_category.covered, ml_category.labelled):>10}"
            f"{_pct_num(ml_category.accuracy):>10}{_pct_num(ml_category.macro_f1):>10}"
        )
    if sentiment is not None:
        lines.append(
            f"{'тональность, модель':<22}{_pct(sentiment.covered, sentiment.labelled):>10}"
            f"{_pct_num(sentiment.accuracy):>10}{_pct_num(sentiment.macro_f1):>10}"
        )
        lines.append("Распределение тональности: " + ", ".join(
            f"{name} {count}" for name, count in sorted(sentiment.distribution.items())
        ))
    return "\n".join(lines)


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole}"


def _pct_num(value: float) -> str:
    return f"{value * 100:.1f}%"
