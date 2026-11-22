from datetime import datetime, timedelta, timezone

from fast_parser.archive import export_before
from fast_parser.models import Article
from fast_parser.timeutil import utcnow
from fast_parser.web.queries import ArticleFilters, search_articles


def test_websearch_or_and_exclude(session, source_feed):
    session, feed = source_feed
    now = datetime.now(timezone.utc)
    _add(session, feed, "https://a.example/1", "Бюджет принят", "Депутаты обсудили бюджет.", now)
    _add(session, feed, "https://a.example/2", "Ставка выросла", "Ключевая ставка изменилась.", now)
    _add(session, feed, "https://a.example/3", "Бюджет и ставка", "Бюджет зависит от ставки.", now)
    session.commit()

    either, total = search_articles(session, ArticleFilters(text="бюджет OR ставка"))
    assert total == 3
    excluded, total = search_articles(session, ArticleFilters(text="бюджет -ставка"))
    assert total == 1
    assert excluded[0][0].canonical_url.endswith("/1")
    phrase, total = search_articles(session, ArticleFilters(text='"ключевая ставка"'))
    assert total == 1


def test_period_preset(session, source_feed):
    session, feed = source_feed
    now = datetime.now(timezone.utc)
    _add(session, feed, "https://a.example/new", "Свежая", "сегодня", now)
    _add(session, feed, "https://a.example/old", "Старая", "давно", now - timedelta(days=40))
    session.commit()
    rows, total = search_articles(session, ArticleFilters(period="7d"))
    assert total == 1
    assert rows[0][0].title == "Свежая"


def test_archive_parquet(session, source_feed, tmp_path):
    session, feed = source_feed
    published = datetime(2020, 3, 2, tzinfo=timezone.utc)
    _add(session, feed, "https://a.example/old", "Архив", "старый текст", published)
    session.commit()
    written = export_before(session, before=datetime(2021, 1, 1).date(), out_dir=tmp_path, drop=True)
    assert written and written[0].suffix == ".parquet"
    assert session.query(Article).count() == 0


def _add(session, feed, url, title, text, published):
    now = utcnow()
    session.add(
        Article(
            source_id=feed.source_id,
            feed_id=feed.id,
            url=url,
            canonical_url=url,
            content_hash=url,
            title=title,
            text=text,
            published_at=published,
            fetched_at=now,
            updated_at=now,
            language="ru",
        )
    )
