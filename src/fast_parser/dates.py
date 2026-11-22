from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

_ISO_START = re.compile(r"^\d{4}-\d{2}-\d{2}")
_NAIVE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
)


def parse_datetime(value: str | None, naive_timezone: str) -> datetime | None:
    """Parse an RSS/Atom date and return an aware UTC datetime."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    parsed = _parse_iso(raw) if _ISO_START.match(raw) else None
    if parsed is None:
        parsed = _parse_rfc822(raw)
    if parsed is None:
        parsed = _parse_iso(raw)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(naive_timezone))
    return parsed.astimezone(timezone.utc)


def _parse_rfc822(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _parse_iso(value: str) -> datetime | None:
    candidate = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass
    for fmt in _NAIVE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
