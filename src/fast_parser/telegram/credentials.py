"""api_id and api_hash from the environment or a local .env file. Never from the repo."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    file = path or Path.cwd() / ".env"
    if not file.exists():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def api_credentials() -> tuple[int, str]:
    load_dotenv()
    raw_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not raw_id or not api_hash:
        raise RuntimeError(
            "Нет TELEGRAM_API_ID и TELEGRAM_API_HASH. "
            "Положите их в .env рядом с запуском или в окружение. "
            "Ключи берутся на https://my.telegram.org и в репозиторий не кладутся."
        )
    try:
        api_id = int(raw_id)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID должен быть числом") from exc
    return api_id, api_hash
