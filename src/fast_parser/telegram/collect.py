"""Catch up Telegram channels from the catalog.

Public channels are read without joining. Without a saved session the public
web preview is used, which only sees the latest page.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_parser.config import AppSettings
from fast_parser.models import Article, Source, TelegramCursor
from fast_parser.pipeline.runner import Pipeline
from fast_parser.storage import upsert_article
from fast_parser.telegram.posts import acceptable, to_item
from fast_parser.timeutil import utcnow
from fast_parser.urls import canonical_url


@dataclass
class TelegramStats:
    channels: int = 0
    ok: int = 0
    error: int = 0
    fetched: int = 0
    new: int = 0
    skipped: int = 0
    duplicate: int = 0
    mode: str = "preview"
    errors: list[tuple[str, str]] = field(default_factory=list)


def collect_telegram(
    settings: AppSettings,
    session: Session,
    *,
    pipeline: Pipeline | None = None,
    preview_only: bool = False,
    pause_seconds: float | None = None,
) -> TelegramStats:
    if not settings.telegram.enabled:
        return TelegramStats(mode="off")
    channels = [
        source
        for source in session.scalars(select(Source).order_by(Source.id))
        if source.telegram_username and (source.telegram_check or "") == "ok"
    ]
    stats = TelegramStats(channels=len(channels))
    session_file = Path(settings.telegram.session_file)
    use_mtproto = not preview_only and session_file.exists() and _telethon_installed()
    stats.mode = "mtproto" if use_mtproto else "preview"
    pause = settings.telegram.pause_seconds if pause_seconds is None else pause_seconds
    for index, source in enumerate(channels):
        if index and pause > 0:
            time.sleep(pause)
        try:
            _one(settings, session, source, stats, use_mtproto=use_mtproto, session_file=session_file, pipeline=pipeline)
            stats.ok += 1
        except Exception as exc:
            stats.error += 1
            stats.errors.append((source.telegram_username or source.id, str(exc)))
            session.rollback()
    return stats


def _one(settings, session, source, stats, *, use_mtproto: bool, session_file: Path, pipeline) -> None:
    cursor = session.get(TelegramCursor, source.id)
    last_id = cursor.last_message_id if cursor else 0
    limit = settings.telegram.catchup_limit if last_id else settings.telegram.first_run_limit
    posts = _fetch(source.telegram_username, last_id=last_id, limit=limit, use_mtproto=use_mtproto, session_file=session_file)
    highest = last_id
    for post in posts:
        highest = max(highest, post["message_id"])
        stats.fetched += 1
        if post["message_id"] <= last_id:
            continue
        if not acceptable(post["text"]):
            stats.skipped += 1
            continue
        if _linked_article(session, post["links"]):
            stats.duplicate += 1
            continue
        item = to_item(
            username=source.telegram_username,
            message_id=post["message_id"],
            text=post["text"],
            published_at=post["published_at"],
            views=post["views"],
            forwards=post.get("forwards"),
            links=post["links"],
        )
        result = upsert_article(session, source_id=source.id, feed_id=None, item=item, language="ru")
        if result.action == "new":
            stats.new += 1
            if pipeline is not None and result.article is not None:
                pipeline.apply(result.article)
        else:
            stats.duplicate += 1
    if cursor is None:
        cursor = TelegramCursor(
            source_id=source.id,
            username=source.telegram_username,
            last_message_id=highest,
            updated_at=utcnow(),
        )
        session.add(cursor)
    else:
        cursor.last_message_id = highest
        cursor.updated_at = utcnow()
    session.commit()


def _fetch(username: str, *, last_id: int, limit: int, use_mtproto: bool, session_file: Path) -> list[dict]:
    if use_mtproto:
        from fast_parser.telegram.mtproto import fetch_history

        return fetch_history(session_file, username, min_id=last_id, limit=limit)
    from fast_parser.telegram.preview import fetch_preview

    channel = fetch_preview(username)
    if not channel.alive:
        raise RuntimeError("публичное превью недоступно")
    posts = [
        {
            "message_id": post.message_id,
            "text": post.text,
            "published_at": post.published_at,
            "views": post.views,
            "forwards": None,
            "links": post.links,
        }
        for post in channel.posts
        if post.message_id > last_id
    ]
    return posts[-limit:]


def _linked_article(session: Session, links: list[str]) -> bool:
    for link in links:
        if "t.me/" in link:
            continue
        found = session.scalar(select(Article.id).where(Article.canonical_url == canonical_url(link)))
        if found is not None:
            return True
    return False


def _telethon_installed() -> bool:
    try:
        import telethon  # noqa: F401
    except ImportError:
        return False
    return True
