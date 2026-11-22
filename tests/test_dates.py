from datetime import datetime, timezone

from fast_parser.dates import parse_datetime

UTC = timezone.utc


def test_rfc822_moscow_offset_becomes_utc():
    parsed = parse_datetime("Sun, 22 Nov 2026 18:56:00 +0300", "Europe/Moscow")
    assert parsed == datetime(2026, 11, 22, 15, 56, tzinfo=UTC)


def test_iso_zulu_and_offset():
    zulu = parse_datetime("2026-11-22T15:00:00Z", "Europe/Moscow")
    offset = parse_datetime("2026-11-22T18:00:00+03:00", "Europe/Moscow")
    assert zulu == datetime(2026, 11, 22, 15, 0, tzinfo=UTC)
    assert offset == datetime(2026, 11, 22, 15, 0, tzinfo=UTC)


def test_naive_timestamp_is_treated_as_moscow():
    parsed = parse_datetime("2026-11-22 18:56:00", "Europe/Moscow")
    assert parsed == datetime(2026, 11, 22, 15, 56, tzinfo=UTC)


def test_russian_dotted_date_and_date_only():
    dotted = parse_datetime("22.11.2026 18:56", "Europe/Moscow")
    assert dotted == datetime(2026, 11, 22, 15, 56, tzinfo=UTC)
    date_only = parse_datetime("2026-11-22", "Europe/Moscow")
    assert date_only == datetime(2026, 11, 21, 21, 0, tzinfo=UTC)


def test_blank_and_garbage():
    assert parse_datetime(None, "Europe/Moscow") is None
    assert parse_datetime("   ", "Europe/Moscow") is None
    assert parse_datetime("не дата", "Europe/Moscow") is None
