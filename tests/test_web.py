from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from fast_parser.config import load_settings
from fast_parser.models import Article, Feed, FeedState
from fast_parser.rss.parser import ParsedItem
from fast_parser.service import open_session
from fast_parser.storage import upsert_article
from fast_parser.timeutil import utcnow
from fast_parser.web.app import create_app

from conftest import settings_for

CATALOG = """
sources:
  - id: south
    name: Южный вестник
    type: газета
    site: https://south.example
    status: ok
    region: Республика Адыгея
    region_code: "01"
    federal_district: Южный
    telegram:
      username: "@south"
      title: Юг
      check: ok
    rss:
      - url: https://south.example/rss.xml
        check: antibot
  - id: center
    name: Центральный вестник
    type: орган_власти
    site: https://center.example
    status: ok
    region: Город Москва
    region_code: "77"
    federal_district: Центральный
    rss: []
"""


def _client(tmp_path):
    settings = settings_for(tmp_path, CATALOG)
    session = open_session(settings)
    try:
        south_feed = session.query(Feed).filter(Feed.source_id == "south").one()
        state = FeedState(
            feed_id=south_feed.id,
            last_status="error",
            last_error="HTTP 503",
            last_http_status=503,
            consecutive_failures=2,
            last_attempt_at=utcnow(),
        )
        session.add(state)
        first = upsert_article(
            session,
            source_id="south",
            feed_id=south_feed.id,
            item=_item("https://south.example/budget", "Бюджет Адыгеи", "Обсудили бюджет республики."),
            language="ru",
        )
        second = upsert_article(
            session,
            source_id="center",
            feed_id=south_feed.id,
            item=_item("https://center.example/metro", "Метро", "Новая станция."),
            language="ru",
        )
        now = datetime.now(timezone.utc)
        first.article.published_at = now
        first.article.category = "экономика"
        first.article.sentiment = "negative"
        first.article.sentiment_score = 0.8
        first.article.fake_score = 0.2
        second.article.published_at = now - timedelta(days=10)
        second.article.sentiment = "positive"
        second.article.fake_score = 0.9
        session.commit()
    finally:
        session.close()
    return TestClient(create_app(settings))


def _item(url: str, title: str, text: str) -> ParsedItem:
    return ParsedItem(
        guid=url,
        url=url,
        title=title,
        summary=text,
        text=text,
        authors=["Редакция"],
        published_at=None,
    )


def test_article_filters_and_detail(tmp_path):
    client = _client(tmp_path)
    page = client.get("/api/articles").json()
    assert page["total"] == 2

    south = client.get("/api/articles", params={"region_code": "01"}).json()
    assert [item["title"] for item in south["items"]] == ["Бюджет Адыгеи"]
    assert south["items"][0]["federal_district"] == "Южный"

    by_type = client.get("/api/articles", params={"source_type": "орган_власти"}).json()
    assert [item["title"] for item in by_type["items"]] == ["Метро"]

    by_district = client.get("/api/articles", params={"federal_district": "Центральный"}).json()
    assert by_district["total"] == 1

    by_text = client.get("/api/articles", params={"q": "БЮДЖЕТ"}).json()
    assert by_text["total"] == 1
    assert by_text["items"][0]["source_id"] == "south"

    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
    by_date = client.get("/api/articles", params={"date_from": today}).json()
    assert {item["title"] for item in by_date["items"]} == {"Бюджет Адыгеи"}

    by_sentiment = client.get("/api/articles", params={"sentiment": "negative"}).json()
    assert by_sentiment["total"] == 1

    by_category = client.get("/api/articles", params={"category": "экономика"}).json()
    assert by_category["total"] == 1

    by_score = client.get("/api/articles", params={"fake_score_min": 0.5}).json()
    assert [item["title"] for item in by_score["items"]] == ["Метро"]

    detail = client.get(f"/api/articles/{south['items'][0]['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["text"] == "Обсудили бюджет республики."
    assert body["authors"] == ["Редакция"]
    assert body["sentiment"] == "negative"
    assert client.get("/api/articles/999999").status_code == 404


def test_sources_regions_stats_and_pages(tmp_path):
    client = _client(tmp_path)
    sources = client.get("/api/sources").json()
    south = next(row for row in sources if row["id"] == "south")
    assert south["telegram_username"] == "@south"
    assert south["feeds"][0]["last_error"] == "HTTP 503"
    assert south["feeds"][0]["enabled"] is False

    regions = client.get("/api/regions").json()
    assert regions["subjects_total"] == 89
    assert regions["subjects_covered"] == 2
    adygea = next(item for item in regions["items"] if item["region_code"] == "01")
    assert adygea["covered"] is True
    assert adygea["telegram"] == 1
    assert adygea["articles"] == 1
    assert adygea["rss_enabled"] == 0
    missing = next(item for item in regions["items"] if item["region_code"] == "02")
    assert missing["covered"] is False

    stats = client.get("/api/stats").json()
    assert stats["articles"] == 2
    assert stats["per_source"][0]["count"] == 1
    assert len(stats["per_day"]) == 2
    assert {row["key"] for row in stats["per_region"]} == {"01", "77"}

    assert client.get("/").status_code == 200
    assert "Бюджет Адыгеи" in client.get("/").text
    assert client.get("/", params={"q": "метро"}).text.find("Метро") != -1
    fragment = client.get("/", headers={"HX-Request": "true"})
    assert fragment.status_code == 200
    assert "<html" not in fragment.text.lower()
    article_id = client.get("/api/articles", params={"q": "метро"}).json()["items"][0]["id"]
    page = client.get(f"/articles/{article_id}")
    assert page.status_code == 200
    assert "fake_score" in page.text
    assert client.get("/sources").status_code == 200
    assert "HTTP 503" in client.get("/sources").text
    regions_page = client.get("/regions")
    assert regions_page.status_code == 200
    assert "89" in regions_page.text
    assert client.get("/stats").status_code == 200
    assert client.get("/articles/999999").status_code == 404
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert "/api/articles" in schema.json()["paths"]
    assert "/api/collect" in schema.json()["paths"]


def test_empty_numeric_fields_still_filter_the_feed(tmp_path):
    client = _client(tmp_path)
    params = {
        "region_code": "01",
        "period": "",
        "date_from": "",
        "date_to": "",
        "sentiment_min": "",
        "sentiment_max": "",
        "category_confidence_min": "",
        "category_confidence_max": "",
        "fake_score_min": "",
        "fake_score_max": "",
        "text_len_min": "",
        "text_len_max": "",
        "min_cluster_sources": "",
        "official": "",
    }
    page = client.get("/", params=params)
    assert page.status_code == 200
    assert "Бюджет Адыгеи" in page.text
    assert "Метро" not in page.text
    assert "Найдено: 1" in page.text
    assert "novalidate" in page.text
    partial = client.get("/", params=params, headers={"HX-Request": "true"})
    assert partial.status_code == 200
    assert "Показать" in partial.text
    assert "Бюджет Адыгеи" in partial.text
    assert "<html" not in partial.text.lower()
    broken = client.get("/", params={"sentiment_min": "нет"})
    assert broken.status_code == 200
    assert "числом" in broken.text


def test_saved_search_and_exports(tmp_path):
    client = _client(tmp_path)
    saved = client.post("/searches", data={"name": "бюджет", "q": "бюджет"}, follow_redirects=False)
    assert saved.status_code == 303
    page = client.get("/")
    assert "бюджет" in page.text
    csv_body = client.get("/export.csv", params={"q": "бюджет"}).content.decode("utf-8")
    assert "Бюджет Адыгеи" in csv_body
    parquet = client.get("/export.parquet", params={"q": "бюджет"})
    assert parquet.status_code == 200
    assert parquet.content[:4] == b"PAR1"


def test_collect_endpoint_runs_without_enabled_feeds(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/collect")
    assert response.status_code == 200
    body = response.json()
    assert body["feeds_total"] == 0
    assert body["items_new"] == 0
    page = client.post("/collect", follow_redirects=False)
    assert page.status_code == 303
    assert "collected=1" in page.headers["location"]
