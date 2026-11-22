from datetime import datetime, timezone

from fast_parser.models import Article, Source
from fast_parser.rss.parser import ParsedItem
from fast_parser.storage import upsert_article
from fast_parser.timeutil import utcnow

UTC = timezone.utc
LONG = "Подробный текст новости. " * 20


def _item(**overrides) -> ParsedItem:
    data = dict(
        guid="g1",
        url="https://alpha.example/news/1?utm_source=rss",
        title="Заголовок",
        summary="Анонс",
        text="Короткий анонс",
        authors=["Редакция"],
        published_at=datetime(2026, 11, 22, 15, 0, tzinfo=UTC),
        categories=["Политика"],
        origin="rss",
        language="ru",
    )
    data.update(overrides)
    return ParsedItem(**data)


def test_utm_url_collapses_to_one_article(source_feed):
    session, feed = source_feed
    first = upsert_article(session, source_id="alpha", feed_id=feed.id, item=_item(), language="ru")
    second = upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=_item(url="https://alpha.example/news/1"),
        language="ru",
    )
    session.commit()
    assert first.action == "new"
    assert second.action == "duplicate"
    assert session.query(Article).count() == 1
    stored = session.query(Article).one()
    assert stored.canonical_url == "https://alpha.example/news/1"
    assert stored.category is None
    assert stored.sentiment is None
    assert stored.fake_score is None
    assert stored.pipeline_status == "pending"
    assert stored.authors == ["Редакция"]
    assert stored.published_at == datetime(2026, 11, 22, 15, 0, tzinfo=UTC)


def test_same_long_body_on_one_source_is_a_duplicate(source_feed):
    session, feed = source_feed
    upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=_item(guid="a", url="https://alpha.example/news/a", text=LONG),
        language="ru",
    )
    again = upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=_item(guid="b", url="https://alpha.example/news/b", text=LONG),
        language="ru",
    )
    session.commit()
    assert again.action == "duplicate"
    assert session.query(Article).count() == 1


def test_same_long_body_on_another_source_is_kept(source_feed):
    session, feed = source_feed
    session.add(
        Source(
            id="beta",
            name="Бета",
            type="test",
            site="https://beta.example",
            status="ok",
            updated_at=utcnow(),
        )
    )
    session.commit()
    upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=_item(url="https://alpha.example/news/a", text=LONG, guid="a"),
        language="ru",
    )
    other = upsert_article(
        session,
        source_id="beta",
        feed_id=feed.id,
        item=_item(url="https://beta.example/news/a", text=LONG, guid="b"),
        language="ru",
    )
    session.commit()
    assert other.action == "new"
    assert session.query(Article).count() == 2


def test_short_items_with_different_urls_both_stay(source_feed):
    session, feed = source_feed
    upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=_item(url="https://alpha.example/news/1", guid="1", text="коротко"),
        language="ru",
    )
    second = upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=_item(url="https://alpha.example/news/2", guid="2", title="Заголовок", text="коротко"),
        language="ru",
    )
    session.commit()
    assert second.action == "new"
    assert session.query(Article).count() == 2


def test_changed_body_updates_row_and_clears_derived_fields(source_feed):
    session, feed = source_feed
    created = upsert_article(session, source_id="alpha", feed_id=feed.id, item=_item(text=LONG), language="ru")
    created.article.category = "экономика"
    created.article.sentiment = "neutral"
    created.article.sentiment_score = 0.1
    created.article.fake_score = 0.4
    created.article.pipeline_status = "processed"
    session.commit()

    updated = upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=_item(text=LONG + " Добавлен новый абзац с фактами.", guid="g1"),
        language="ru",
    )
    session.commit()
    assert updated.action == "updated"
    assert session.query(Article).count() == 1
    stored = session.query(Article).one()
    assert "Добавлен новый абзац" in stored.text
    assert stored.category is None
    assert stored.sentiment is None
    assert stored.fake_score is None
    assert stored.pipeline_status == "pending"


def test_same_guid_with_a_new_url_updates_in_place(source_feed):
    session, feed = source_feed
    upsert_article(session, source_id="alpha", feed_id=feed.id, item=_item(), language="ru")
    moved = upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=_item(url="https://alpha.example/news/1-new", text=LONG),
        language="ru",
    )
    session.commit()
    assert moved.action == "updated"
    stored = session.query(Article).one()
    assert stored.canonical_url == "https://alpha.example/news/1-new"
    assert stored.guid == "g1"
