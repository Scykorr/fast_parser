"""Lexical score for the fixed taxonomy. No model required."""

from __future__ import annotations

import re

from fast_parser.pipeline.taxonomy import CategorySpec


def lexical_scores(
    *,
    title: str,
    text: str,
    rss_categories: tuple[str, ...] | list[str],
    taxonomy: list[CategorySpec],
) -> dict[str, float]:
    title_n = title.casefold()
    text_n = text.casefold()
    rss_n = [label.casefold() for label in rss_categories if label]
    scores: dict[str, float] = {}
    for spec in taxonomy:
        weight = 0.0
        for label in spec.rss:
            needle = label.casefold()
            if any(needle in item or item in needle for item in rss_n):
                weight += 3.0
        for keyword in spec.keywords:
            if _contains(keyword, title_n):
                weight += 2.0
            elif _contains(keyword, text_n):
                weight += 1.0
        scores[spec.title] = min(1.0, weight / 4.0)
    return scores


def _contains(keyword: str, haystack: str) -> bool:
    needle = keyword.casefold().strip()
    if not needle or not haystack:
        return False
    if len(needle) <= 4:
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None
    return needle in haystack
