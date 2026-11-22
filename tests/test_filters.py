from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from fast_parser.models import Article, Feed, Source
from fast_parser.search_filters import ArticleFilters, facet_counts, parse_filters
from fast_parser.timeutil import utcnow
from fast_parser.web.app import create_app
from fast_parser.web.queries import search_articles

from conftest import settings_for


def test_include_exclude_and_ranges_combine(session):
    now = datetime.now(timezone.utc)
    _source(session, "south", "Юг", "газета", "ok", "01", "Адыгея", "Южный")
    _source(session, "north", "Север", "телеканал", "blocked", "02", "Башкортостан", "Приволжский")
    feed = Feed(source_id="south", url="https://south.example/rss", enabled=True)
    session.add(feed)
    session.flush()
    _article(
        session,
        "south",
        feed.id,
        "https://south.example/a",
        "Бюджет Адыгеи",
        "Обсудили бюджет республики и больше ничего.",
        now,
        category="Экономика",
        sentiment="negative",
        sentiment_score=0.9,
        category_confidence=0.8,
        authors=["Иван Иванов"],
        tags=["Политика"],
        language="ru",
        pipeline_status="processed",
        fake_score=0.2,
        fake_label="unchecked",
        content_hash="same",
        text_length=42,
    )
    _article(
        session,
        "north",
        feed.id,
        "https://north.example/b",
        "Метро",
        "Новая станция метро без бюджета.",
        now - timedelta(days=3),
        category="Спорт",
        sentiment="positive",
        sentiment_score=0.2,
        category_confidence=0.3,
        authors=["Мария"],
        tags=["Спорт"],
        language="en",
        pipeline_status="pending",
        content_origin="telegram",
        content_hash="same",
        domain="north.example",
        fetched_at=now - timedelta(days=10),
    )
    _article(
        session,
        "south",
        feed.id,
        "https://south.example/c",
        "Третья",
        "Коротко",
        now - timedelta(days=40),
        category="Экономика",
        sentiment="negative",
        sentiment_score=0.95,
        category_confidence=0.9,
        content_hash="other",
        text_length=6,
    )
    session.commit()

    both, total = search_articles(
        session,
        ArticleFilters(categories=["Экономика", "Спорт"], categories_not=["Спорт"]),
    )
    assert total == 2
    assert {row[0].title for row in both} == {"Бюджет Адыгеи", "Третья"}

    combo, total = search_articles(
        session,
        ArticleFilters(
            categories=["Экономика"],
            region_codes=["01"],
            sentiments=["negative"],
            sentiment_min=0.5,
            category_confidence_min=0.7,
            languages=["ru"],
            pipeline_statuses=["processed"],
            authors=["Иван"],
            tags=["Политика"],
            tags_not=["Спорт"],
            domains=["south.example"],
            text_len_min=20,
            fake_score_max=0.5,
            fake_labels=["unchecked"],
            official=True,
            channels=["rss"],
        ),
    )
    assert total == 1
    assert combo[0][0].title == "Бюджет Адыгеи"

    title_only, total = search_articles(session, ArticleFilters(title_text="бюджет", body_text="-республики"))
    assert total == 0
    title_only, total = search_articles(session, ArticleFilters(title_text="бюджет"))
    assert total == 1
    body_only, total = search_articles(session, ArticleFilters(body_text="станция"))
    assert [row[0].title for row in body_only] == ["Метро"]

    cluster, total = search_articles(session, ArticleFilters(min_cluster_sources=2))
    assert {row[0].title for row in cluster} == {"Бюджет Адыгеи", "Метро"}

    collected, total = search_articles(
        session,
        ArticleFilters(date_on="collected", period="7d"),
    )
    assert "Метро" not in {row[0].title for row in collected}

    recent, total = search_articles(session, ArticleFilters(period="24h"))
    assert {row[0].title for row in recent} == {"Бюджет Адыгеи"}

    sunday = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    timed = session.get(Article, combo[0][0].id)
    timed.published_at = sunday
    session.commit()
    by_clock, total = search_articles(session, ArticleFilters(weekdays=[7], hours=[15]))
    assert total == 1

    facets = facet_counts(session, ArticleFilters(sentiments=["negative"]))
    categories = {item["value"]: item["count"] for item in facets["category"]}
    assert categories["Экономика"] == 2
    assert "Спорт" not in categories
    sentiments = {item["value"]: item["count"] for item in facets["sentiment"]}
    assert sentiments["positive"] == 1
    assert sentiments["negative"] == 2


def test_api_repeats_exclude_and_documents_facets(tmp_path):
    settings = settings_for(tmp_path, "sources: []\n")
    client = TestClient(create_app(settings))
    page = client.get(
        "/api/articles",
        params=[("category", "Экономика"), ("category", "Спорт"), ("category_not", "Спорт")],
    )
    assert page.status_code == 200
    schema = client.get("/openapi.json").json()
    params = schema["paths"]["/api/articles"]["get"]["parameters"]
    names = {item["name"] for item in params}
    assert {"category", "category_not", "channel", "weekday", "min_cluster_sources", "title_q"} <= names
    facets = client.get("/api/facets")
    assert facets.status_code == 200
    assert "category" in facets.json()["facets"]


def test_query_parser_matches_the_dataclass():
    parsed = parse_filters(
        {
            "source_id": ["tass", "ria"],
            "source_id_not": ["lenta"],
            "period": "24h",
            "date_on": "collected",
            "weekday": ["1", "7"],
            "official": "yes",
        }
    )
    direct = ArticleFilters(
        sources=["tass", "ria"],
        sources_not=["lenta"],
        period="24h",
        date_on="collected",
        weekdays=[1, 7],
        official=True,
    )
    assert parsed.to_query()["source_id"] == direct.to_query()["source_id"]
    assert parsed.official is True
    assert parsed.date_on == "collected"
    assert parsed.weekdays == [1, 7]


def _source(session, source_id, name, kind, status, code, region, district):
    session.add(
        Source(
            id=source_id,
            name=name,
            type=kind,
            site=f"https://{source_id}.example",
            status=status,
            region=region,
            region_code=code,
            federal_district=district,
            updated_at=utcnow(),
        )
    )
    session.flush()


def _article(session, source_id, feed_id, url, title, text, published, **extra):
    now = utcnow()
    session.add(
        Article(
            source_id=source_id,
            feed_id=feed_id,
            url=url,
            canonical_url=url,
            content_hash=extra.pop("content_hash", url),
            title=title,
            text=text,
            published_at=published,
            fetched_at=extra.pop("fetched_at", now),
            updated_at=now,
            language=extra.pop("language", "ru"),
            domain=extra.pop("domain", f"{source_id}.example"),
            content_origin=extra.pop("content_origin", "rss"),
            pipeline_status=extra.pop("pipeline_status", "pending"),
            text_length=extra.pop("text_length", len(text)),
            **extra,
        )
    )
