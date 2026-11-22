"""Turn a Telegram post into an article, and drop ads and scraps."""

from __future__ import annotations

import re

from fast_parser.rss.parser import ParsedItem

_AD = ("#реклама", "на правах рекламы", "erid:", "erid ")
_MIN_CHARS = 40


def acceptable(text: str) -> bool:
    body = text.strip()
    if len(body) < _MIN_CHARS:
        return False
    folded = body.casefold()
    return not any(mark in folded for mark in _AD)


def to_item(
    *,
    username: str,
    message_id: int,
    text: str,
    published_at,
    views: int | None,
    forwards: int | None,
    links: list[str],
) -> ParsedItem:
    name = username.lstrip("@")
    url = f"https://t.me/{name}/{message_id}"
    title = _title(text)
    extra = {
        "telegram_message_id": message_id,
        "views": views,
        "forwards": forwards,
        "links": links,
    }
    return ParsedItem(
        guid=f"telegram:{name}:{message_id}",
        url=url,
        title=title,
        summary=title,
        text=text.strip(),
        authors=[],
        published_at=published_at,
        categories=[],
        origin="telegram",
        language="ru",
        extra=extra,
    )


def _title(text: str) -> str:
    line = text.strip().splitlines()[0].strip()
    line = re.sub(r"\s+", " ", line)
    if len(line) > 180:
        return line[:177].rstrip() + "…"
    return line
