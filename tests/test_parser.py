from datetime import datetime, timezone

from fast_parser.rss.parser import FeedParseError, parse_feed

from conftest import FIXTURES

UTC = timezone.utc
TZ = "Europe/Moscow"


def test_rss_fulltext_authors_and_utc_date():
    items = parse_feed((FIXTURES / "rss_full.xml").read_bytes(), naive_timezone=TZ)
    assert len(items) == 1
    item = items[0]
    assert item.title == "Полный материал"
    assert item.url.endswith("utm_source=rss&utm_medium=feed")
    assert item.guid == "guid-1"
    assert item.authors == ["Иван Иванов"]
    assert item.categories == ["Политика"]
    assert item.summary == "Короткий анонс."
    assert "Это полный текст статьи." in item.text
    assert "Второй абзац про бюджет." in item.text
    assert "<p>" not in item.text
    assert item.published_at == datetime(2026, 11, 22, 15, 56, tzinfo=UTC)
    assert item.language == "ru"


def test_announcement_and_missing_date():
    items = parse_feed((FIXTURES / "rss_announcement.xml").read_text(encoding="utf-8"), naive_timezone=TZ)
    assert [item.url for item in items] == [
        "https://example.com/news/2",
        "https://example.com/news/3",
    ]
    assert items[0].text == "Коротко о событии."
    assert items[0].published_at == datetime(2026, 11, 22, 15, 0, tzinfo=UTC)
    assert items[1].published_at is None


def test_atom_entry():
    items = parse_feed((FIXTURES / "atom_feed.xml").read_bytes(), naive_timezone=TZ)
    assert len(items) == 1
    item = items[0]
    assert item.title == "Атомная запись"
    assert item.url == "https://example.com/atom/1"
    assert item.guid == "urn:example:atom:1"
    assert item.authors == ["Редакция"]
    assert item.text == "Текст атомной записи."
    assert item.published_at == datetime(2026, 11, 22, 15, 0, tzinfo=UTC)


def test_html_challenge_is_not_an_empty_feed():
    payload = (FIXTURES / "not_a_feed.html").read_bytes()
    try:
        parse_feed(payload, naive_timezone=TZ)
    except FeedParseError as exc:
        assert str(exc)
    else:
        raise AssertionError("HTML must not be parsed as an empty feed")


def test_windows_1251_bytes():
    xml = """<?xml version="1.0" encoding="windows-1251"?>
    <rss version="2.0"><channel><title>Лента</title>
      <item>
        <title>Новость дня</title>
        <link>https://example.com/n</link>
        <description>Текст новости</description>
        <pubDate>Sun, 22 Nov 2026 12:00:00 +0300</pubDate>
      </item>
    </channel></rss>"""
    items = parse_feed(xml.encode("cp1251"), naive_timezone=TZ)
    assert items[0].title == "Новость дня"
    assert items[0].text == "Текст новости"
    assert items[0].published_at == datetime(2026, 11, 22, 9, 0, tzinfo=UTC)


def test_valid_empty_channel_has_no_items():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Пусто</title><description>ok</description></channel></rss>"""
    assert parse_feed(xml, naive_timezone=TZ) == []
