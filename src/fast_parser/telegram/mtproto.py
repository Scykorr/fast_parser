"""Read-only Telethon access. The collector never joins a channel and never sends a message."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fast_parser.telegram.credentials import api_credentials


def login(session_file: Path) -> None:
    client = _client(session_file)
    phone = input("Телефон в международном формате: ").strip()
    client.start(
        phone=lambda: phone,
        code_callback=lambda: input("Код из Telegram: ").strip(),
        password=lambda: _password(),
    )
    print("Сессия сохранена:", session_file)
    client.disconnect()


def fetch_history(session_file: Path, username: str, *, min_id: int, limit: int) -> list[dict]:
    client = _client(session_file)
    try:
        with client:
            return client.loop.run_until_complete(
                _history(client, username, min_id=min_id, limit=limit)
            )
    except Exception:
        if client.is_connected():
            client.disconnect()
        raise


def _client(session_file: Path):
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            'Пакет Telethon не установлен. Поставьте extra: pip install -e ".[telegram]"'
        ) from exc
    api_id, api_hash = api_credentials()
    session_file.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(session_file), api_id, api_hash)


def _password() -> str:
    import getpass

    return getpass.getpass("Пароль двухфакторной защиты, если он есть: ")


async def _history(client, username: str, *, min_id: int, limit: int) -> list[dict]:
    from telethon.errors import FloodWaitError

    name = username.lstrip("@")
    try:
        messages = await client.get_messages(name, limit=limit, min_id=min_id)
    except FloodWaitError as exc:
        if exc.seconds > 90:
            raise RuntimeError(f"FloodWait {exc.seconds} с, канал пропущен") from exc
        await asyncio.sleep(exc.seconds + 1)
        messages = await client.get_messages(name, limit=limit, min_id=min_id)
    found = []
    seen_groups: set[int] = set()
    for message in messages:
        if getattr(message, "action", None):
            continue
        grouped = getattr(message, "grouped_id", None)
        text = message.message or ""
        if grouped:
            if grouped in seen_groups:
                continue
            seen_groups.add(grouped)
        links = []
        for entity in message.entities or []:
            url = getattr(entity, "url", None)
            if url:
                links.append(url)
        found.append(
            {
                "message_id": int(message.id),
                "text": text,
                "published_at": message.date,
                "views": getattr(message, "views", None),
                "forwards": getattr(message, "forwards", None),
                "links": links,
            }
        )
    found.sort(key=lambda item: item["message_id"])
    return found
