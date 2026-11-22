"""Features for the linear heads. Training and inference must build the same vector."""

from __future__ import annotations

import math


def head_text(title: str, text: str) -> str:
    return f"{title}. {(text or '')[:1200]}"


def category_feature(vector: list[float], prototypes: dict[str, float], lexical: dict[str, float], labels: list[str]) -> list[float]:
    return _normalized(vector) + [prototypes.get(label, 0.0) for label in labels] + [lexical.get(label, 0.0) for label in labels]


def sentiment_feature(vector: list[float]) -> list[float]:
    return _normalized(vector)


def _normalized(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
