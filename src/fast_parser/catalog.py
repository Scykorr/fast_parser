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

`config/sources_regional.yaml` is a second snapshot: one or more official
sources for every federal subject. It is merged at load time. Regional
fields stay empty on the federal snapshot.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

COLLECTABLE_CHECKS = frozenset({"ok", "blocked_from_box_verified"})

# FNS subject codes (order ED-1-3/117@, 2026-02-24): 01–79, the autonomous
# okrugs that keep their own code, Crimea, Sevastopol and the four subjects
# added in 2022. 80–82, 84, 85 and 88 are not federal subjects.
FEDERAL_SUBJECT_CODES = frozenset(
    [f"{number:02d}" for number in range(1, 80)]
    + ["83", "86", "87", "89", "90", "91", "92", "93", "94", "95"]
)


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
    region: str | None = None
    region_code: str | None = None
    federal_district: str | None = None
    rss: list[RssFeed] = Field(default_factory=list)
    telegram: TelegramChannel | None = None
    notes: str | None = None
    role: str | None = None

    def resolved_role(self) -> str:
        if self.role in {"media", "authority"}:
            return self.role
        return "authority" if self.type == "орган_власти" else "media"

    @field_validator("region_code", mode="before")
    @classmethod
    def _normalize_code(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit() and len(text) < 2:
            return text.zfill(2)
        return text


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
    _reject_duplicate_ids(catalog.sources)
    return catalog


def load_merged_catalog(paths: list[Path]) -> Catalog:
    """Load several snapshots into one catalog. Ids must be unique across files."""
    sources: list[SourceSpec] = []
    for path in paths:
        sources.extend(load_catalog(path).sources)
    _reject_duplicate_ids(sources)
    return Catalog(sources=sources)


def _reject_duplicate_ids(sources: list[SourceSpec]) -> None:
    ids = [source.id for source in sources]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"Повторяющиеся id источников: {', '.join(duplicates)}")


def region_codes(catalog: Catalog) -> set[str]:
    return {source.region_code for source in catalog.sources if source.region_code}


def missing_subject_codes(catalog: Catalog) -> list[str]:
    """Subject codes from the FNS list that have no source in this catalog."""
    return sorted(FEDERAL_SUBJECT_CODES - region_codes(catalog))


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
