"""Source catalog.

`config/sources.yaml` is the verification snapshot (32 state-media and
government sources). A feed is collected only when its `check` says the
feed itself is alive:

* `ok` — fetched during verification
* `blocked_from_box_verified` — blocked from the verification host, but
  confirmed by another fetch. The laptop runs from a Russian IP, so these
  feeds are enabled.

`empty`, `antibot` and `unverified` stay in the catalog (and so do Telegram
usernames) but are not requested. Extra URLs and source ids can be disabled
from settings without editing the snapshot.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

COLLECTABLE_CHECKS = frozenset({"ok", "blocked_from_box_verified"})


class TelegramChannel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: str | None = None
    title: str | None = None
    check: str | None = None
    latest: str | None = None
    messages_on_preview: int | None = None
    aliases: list[str] = Field(default_factory=list)
    note: str | None = None


class RssFeed(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    title: str | None = None
    items: int | None = None
    latest: str | None = None
    fulltext: str | None = None
    check: str | None = None
    http_code: int | None = None
    median_desc_chars: int | None = None
    note: str | None = None


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    type: str
    site: str
    status: str
    rss: list[RssFeed] = Field(default_factory=list)
    telegram: TelegramChannel | None = None
    notes: str | None = None


class Catalog(BaseModel):
    model_config = ConfigDict(extra="ignore")

    generated_at: str | None = None
    verification_agent: str | None = None
    sources: list[SourceSpec]


def load_catalog(path: Path) -> Catalog:
    if not path.exists():
        raise FileNotFoundError(f"Каталог источников не найден: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    catalog = Catalog.model_validate(loaded)
    ids = [source.id for source in catalog.sources]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"Повторяющиеся id источников: {', '.join(duplicates)}")
    return catalog


def feed_is_collected(
    feed: RssFeed,
    *,
    source_id: str,
    skip_feed_urls: set[str],
    skip_source_ids: set[str],
) -> bool:
    if source_id in skip_source_ids:
        return False
    if feed.url in skip_feed_urls:
        return False
    return (feed.check or "") in COLLECTABLE_CHECKS
