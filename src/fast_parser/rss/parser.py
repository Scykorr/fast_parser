from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser

from fast_parser.dates import parse_datetime
from fast_parser.textutil import html_to_text


class FeedParseError(ValueError):
    """The response was not a usable feed."""


@dataclass
class ParsedItem:
    guid: str | None
    url: str
    title: str
    summary: str
    text: str
    authors: list[str]
    published_at: datetime | None
    categories: list[str] = field(default_factory=list)
    origin: str = "rss"
    language: str | None = None


def parse_feed(payload: bytes | str, *, naive_timezone: str) -> list[ParsedItem]:
    parsed = feedparser.parse(payload)
    entries = list(parsed.entries or [])
    if not entries:
        # An empty but real channel is a successful fetch with nothing new.
        # Antibot HTML and other non-feeds are errors, even if the parser
        # only reports them as "bozo" with zero entries.
        if _looks_like_feed(payload):
            return []
        exc = getattr(parsed, "bozo_exception", None)
        detail = str(exc).strip() if exc else "ответ не похож на RSS или Atom"
        raise FeedParseError(detail)

    channel_language = _clean(parsed.feed.get("language")) if parsed.feed else None
    items: list[ParsedItem] = []
    for entry in entries:
        url = _entry_url(entry)
        if not url:
            continue
        summary_html = entry.get("summary") or entry.get("description") or ""
        content_html = _longest_content(entry.get("content") or [])
        body_html = content_html or summary_html
        authors = _authors(entry)
        published = _published(entry, naive_timezone)
        guid = _clean(entry.get("id") or entry.get("guid"))
        categories = []
        for tag in entry.get("tags") or []:
            term = _clean(tag.get("term") if isinstance(tag, dict) else None)
            if term and term not in categories:
                categories.append(term)
        items.append(
            ParsedItem(
                guid=guid,
                url=url,
                title=html_to_text(entry.get("title") or ""),
                summary=html_to_text(summary_html),
                text=html_to_text(body_html),
                authors=authors,
                published_at=published,
                categories=categories,
                language=channel_language,
            )
        )
    return items


def _entry_url(entry) -> str:
    link = _clean(entry.get("link"))
    if link:
        return link
    for candidate in entry.get("links") or []:
        href = _clean(candidate.get("href") if isinstance(candidate, dict) else None)
        rel = (candidate.get("rel") if isinstance(candidate, dict) else "") or ""
        if href and rel in {"", "alternate"}:
            return href
    return ""


def _longest_content(blocks: list) -> str:
    best = ""
    for block in blocks:
        if not isinstance(block, dict):
            continue
        value = block.get("value") or ""
        if len(value) > len(best):
            best = value
    return best


def _authors(entry) -> list[str]:
    found: list[str] = []
    primary = html_to_text(entry.get("author") or "")
    if primary:
        found.append(primary)
    for author in entry.get("authors") or []:
        if not isinstance(author, dict):
            continue
        name = html_to_text(author.get("name") or "")
        if name and name not in found:
            found.append(name)
    return found


def _published(entry, naive_timezone: str) -> datetime | None:
    for key in ("published", "updated", "created"):
        parsed = parse_datetime(entry.get(key), naive_timezone)
        if parsed is not None:
            return parsed
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            # feedparser's *_parsed tuple is already UTC.
            return datetime(*struct[:6], tzinfo=timezone.utc)
    return None


def _looks_like_feed(payload: bytes | str) -> bool:
    if isinstance(payload, bytes):
        sample = payload[:4000].decode("utf-8", errors="ignore")
        if not any(marker in sample.lower() for marker in ("<rss", "<feed", "<channel")):
            sample = payload[:4000].decode("cp1251", errors="ignore")
    else:
        sample = payload[:4000]
    lowered = sample.lower()
    return "<rss" in lowered or "<feed" in lowered or "<channel" in lowered


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
