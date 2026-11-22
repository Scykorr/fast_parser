"""Public web preview at t.me/s/<channel>. No account and no join."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class PreviewPost:
    message_id: int
    text: str
    published_at: datetime | None
    views: int | None
    links: list[str]


@dataclass
class PreviewChannel:
    username: str
    alive: bool
    title: str
    posts: list[PreviewPost]


def fetch_preview(username: str, *, client: httpx.Client | None = None) -> PreviewChannel:
    name = username.lstrip("@")
    owns = client is None
    http = client or httpx.Client(headers={"User-Agent": _USER_AGENT}, follow_redirects=True, timeout=30)
    try:
        response = http.get(f"https://t.me/s/{name}")
        response.raise_for_status()
        return parse_preview(name, response.text)
    finally:
        if owns:
            http.close()


def parse_preview(username: str, html: str) -> PreviewChannel:
    alive = "tgme_widget_message" in html and "tgme_channel_info_header_title" in html
    title = _channel_title(html)
    posts = _posts(html) if alive else []
    return PreviewChannel(username=username, alive=alive, title=title, posts=posts)


def _channel_title(html: str) -> str:
    match = re.search(r"<title>(.*?) – Telegram</title>", html)
    if not match:
        return ""
    return _unescape(re.sub(r"<.*?>", "", match.group(1))).strip()


def _posts(html: str) -> list[PreviewPost]:
    chunks = re.split(r'(?=<div class="tgme_widget_message_wrap)', html)
    posts: list[PreviewPost] = []
    for chunk in chunks:
        post = re.search(r'data-post="[^"/]+/(\d+)"', chunk)
        if not post:
            continue
        text = _message_text(chunk)
        stamp = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        views = re.search(r'class="tgme_widget_message_views"[^>]*>([^<]+)', chunk)
        links = re.findall(r'<a[^>]+href="(https?://[^"]+)"', _message_html(chunk))
        published = None
        if stamp:
            try:
                published = datetime.fromisoformat(stamp.group(1))
            except ValueError:
                published = None
        posts.append(
            PreviewPost(
                message_id=int(post.group(1)),
                text=text,
                published_at=published,
                views=_views(views.group(1) if views else None),
                links=[_unescape(link) for link in links],
            )
        )
    posts.sort(key=lambda item: item.message_id)
    return posts


def _message_html(chunk: str) -> str:
    match = re.search(
        r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        chunk,
        re.DOTALL,
    )
    return match.group(1) if match else ""


def _message_text(chunk: str) -> str:
    raw = _message_html(chunk)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"</p>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<tg-emoji[^>]*>.*?</tg-emoji>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"<.*?>", "", raw)
    return _unescape(raw).strip()


def _views(raw: str | None) -> int | None:
    if not raw:
        return None
    text = raw.strip().replace(" ", "").replace(",", ".")
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)([KMB])?", text, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    factor = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    return int(number * factor)


def _unescape(value: str) -> str:
    return (
        value.replace("&quot;", '"')
        .replace("&#33;", "!")
        .replace("&#39;", "'")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("\xa0", " ")
    )

