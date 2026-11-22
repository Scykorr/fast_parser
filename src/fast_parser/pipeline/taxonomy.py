from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CategorySpec:
    id: str
    title: str
    description: str
    keywords: tuple[str, ...]
    rss: tuple[str, ...]


def load_taxonomy(path: Path) -> list[CategorySpec]:
    if not path.exists():
        raise FileNotFoundError(f"Файл рубрик не найден: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = loaded.get("categories") or []
    categories: list[CategorySpec] = []
    seen: set[str] = set()
    for item in raw:
        title = str(item["title"]).strip()
        if title in seen:
            raise ValueError(f"Повторяется рубрика {title}")
        seen.add(title)
        categories.append(
            CategorySpec(
                id=str(item.get("id") or title),
                title=title,
                description=str(item.get("description") or title),
                keywords=tuple(str(word) for word in item.get("keywords") or []),
                rss=tuple(str(label) for label in item.get("rss") or []),
            )
        )
    if not categories:
        raise ValueError(f"В {path} нет рубрик")
    return categories
