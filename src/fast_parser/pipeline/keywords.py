"""Lexical score for the fixed taxonomy. No model required."""

from __future__ import annotations

import re

from fast_parser.pipeline.taxonomy import CategorySpec

# GTRK and agencies stamp these on almost every item. They are not a topic.
_GENERIC_RSS = (
    "новости",
    "лента",
    "видео",
    "index",
    "интересное",
    "событие дня",
    "format-article",
    "новости дня",
)
# A real section, but many regional feeds put everything here.
_WEAK_RSS = ("общество",)


def lexical_scores(
    *,
    title: str,
    text: str,
    rss_categories: tuple[str, ...] | list[str],
    taxonomy: list[CategorySpec],
) -> dict[str, float]:
    title_n = title.casefold()
    text_n = text.casefold()
    rss_n = [_clean_rss(label) for label in rss_categories if label]
    rss_n = [item for item in rss_n if item and not _is_generic(item)]
    scores: dict[str, float] = {}
    for spec in taxonomy:
        weight = 0.0
        title_hit = False
        body_hit = False
        for label in spec.rss:
            needle = label.casefold()
            if any(needle in item or item in needle for item in rss_n):
                weight += 1.0 if needle in _WEAK_RSS else 4.0
        for keyword in spec.keywords:
            if _contains(keyword, title_n):
                title_hit = True
            elif not body_hit and _contains(keyword, text_n):
                body_hit = True
        if title_hit:
            weight += 3.0
        if body_hit:
            weight += 1.0
        scores[spec.title] = min(1.0, weight / 4.0)
    return scores


def _clean_rss(label: str) -> str:
    return label.casefold().strip()


def _is_generic(label: str) -> bool:
    return any(generic in label for generic in _GENERIC_RSS)


def _contains(keyword: str, haystack: str) -> bool:
    needle = keyword.casefold().strip()
    if not needle or not haystack:
        return False
    if len(needle) <= 4:
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None
    return needle in haystack
