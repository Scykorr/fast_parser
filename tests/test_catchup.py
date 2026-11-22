import asyncio
from datetime import datetime, timezone

import httpx

from fast_parser.models import Article, FeedState
from fast_parser.service import collect_enabled, open_session
from fast_parser.storage import iter_export_dicts

from conftest import settings_for

UTC = timezone.utc
LONG = "Подробный текст материала. " * 30

CATALOG = """
sources:
  - id: alpha
    name: Альфа
    type: test
    site: https://alpha.example
    status: ok
    telegram:
      username: "@alpha"
      title: Альфа
      check: ok
    rss:
      - url: https://alpha.example/rss.xml
        title: Alpha
        check: ok
      - url: https://alpha.example/broken.xml
        check: ok
  - id: beta
    name: Бета
    type: test
    site: https://beta.example
    status: telegram_ok_rss_blocked
    telegram:
      username: "@beta"
      title: Бета
      check: ok
    rss:
      - url: https://beta.example/rss.xml
        check: antibot
"""


def _rss(*items: str) -> bytes:
    body = "\n".join(items)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Альфа</title><language>ru</language>
    {body}
    </channel></rss>"""
    return xml.encode("utf-8")


def _item(guid: str, url: str, title: str, when: str, description: str) -> str:
    return f"""<item>
      <title>{title}</title>
      <link>{url}</link>
      <guid>{guid}</guid>
      <pubDate>{when}</pubDate>
      <description>{description}</description>
    </item>"""


BASE = _rss(
    _item(
        "1",
        "https://alpha.example/news/1?utm_source=rss",
        "Первая",
        "Sun, 22 Nov 2026 18:00:00 +0300",
        "Короткий анонс первой.",
    ),
    _item(
        "2",
        "https://alpha.example/news/2",
        "Вторая",
        "Sun, 22 Nov 2026 19:00:00 +0300",
        LONG,
    ),
)
GROWN = _rss(
    _item(
        "1",
        "https://alpha.example/news/1?utm_source=rss",
        "Первая",
        "Sun, 22 Nov 2026 18:00:00 +0300",
        "Короткий анонс первой.",
    ),
    _item(
        "2",
        "https://alpha.example/news/2",
        "Вторая",
        "Sun, 22 Nov 2026 19:00:00 +0300",
        LONG,
    ),
    _item(
        "3",
        "https://alpha.example/news/3",
        "Третья",
        "Sun, 22 Nov 2026 20:30:00 +0300",
        LONG + " Новое событие.",
    ),
)


class _World:
    def __init__(self):
        self.mode = "base"
        self.alpha_hits = 0
        self.broken_hits = 0
        self.conditional = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "beta.example" in url:
            raise AssertionError(f"disabled feed was requested: {url}")
        if url.endswith("/broken.xml"):
            self.broken_hits += 1
            return httpx.Response(503, text="unavailable")
        if url.endswith("/rss.xml"):
            self.alpha_hits += 1
            inm = request.headers.get("if-none-match")
            if self.mode == "base":
                assert inm is None
                return httpx.Response(
                    200,
                    headers={"ETag": 'W/"v1"', "Last-Modified": "Sun, 22 Nov 2026 16:00:00 GMT"},
                    content=BASE,
                )
            if self.mode == "same":
                self.conditional = inm
                assert request.headers.get("if-modified-since")
                return httpx.Response(304)
            if self.mode == "down":
                raise httpx.ConnectError("TLS unexpected eof")
            return httpx.Response(200, headers={"ETag": 'W/"v2"'}, content=GROWN)
        return httpx.Response(404, text=url)


def _collect(settings, session, world, extractor=None):
    async def run():
        transport = httpx.MockTransport(world.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await collect_enabled(settings, session, client=client, extractor=extractor)

    return asyncio.run(run())


def test_catchup_dedupes_conditional_get_and_survives_feed_errors(tmp_path):
    settings = settings_for(tmp_path, CATALOG)
    session = open_session(settings)
    world = _World()
    try:
        first = _collect(settings, session, world)
        assert first.feeds_total == 2
        assert first.feeds_ok == 1
        assert first.feeds_error == 1
        assert first.items_new == 2
        assert first.items_duplicate == 0
        assert world.broken_hits == 2  # one try plus one retry
        assert session.query(Article).count() == 2

        stored_urls = {row.canonical_url for row in session.query(Article)}
        assert stored_urls == {
            "https://alpha.example/news/1",
            "https://alpha.example/news/2",
        }
        state = session.query(FeedState).filter(FeedState.newest_guid == "2").one()
        assert state.etag == 'W/"v1"'
        assert state.newest_published_at == datetime(2026, 11, 22, 16, 0, tzinfo=UTC)
        assert state.consecutive_failures == 0
        broken = session.query(FeedState).filter(FeedState.last_status == "error").one()
        assert broken.consecutive_failures == 1
        assert "503" in (broken.last_error or "")

        exported = list(iter_export_dicts(session))
        assert len(exported) == 2
        assert set(exported[0]) == {
            "source",
            "source_name",
            "url",
            "published_at",
            "authors",
            "title",
            "text",
            "category",
            "sentiment",
            "fake_score",
        }
        assert exported[0]["source"] == "alpha"
        assert exported[0]["source_name"] == "Альфа"
        assert exported[0]["category"] is None
        assert exported[0]["sentiment"] is None
        assert exported[0]["fake_score"] is None

        world.mode = "same"
        second = _collect(settings, session, world)
        assert second.feeds_not_modified == 1
        assert second.items_new == 0
        assert second.items_fetched == 0
        assert world.conditional == 'W/"v1"'
        assert session.query(Article).count() == 2
        assert state.newest_published_at == datetime(2026, 11, 22, 16, 0, tzinfo=UTC)

        world.mode = "grown"
        third = _collect(settings, session, world)
        assert third.items_new == 1
        assert third.items_duplicate == 2
        assert session.query(Article).count() == 3
        session.refresh(state)
        assert state.etag == 'W/"v2"'
        assert state.newest_published_at == datetime(2026, 11, 22, 17, 30, tzinfo=UTC)
        assert state.newest_guid == "3"

        world.mode = "down"
        before = world.alpha_hits
        fourth = _collect(settings, session, world)
        assert fourth.feeds_error == 2
        assert session.query(Article).count() == 3
        assert world.alpha_hits == before + 2
        session.refresh(state)
        assert state.etag == 'W/"v2"'
        assert state.consecutive_failures == 1
        assert "TLS" in (state.last_error or "")
    finally:
        session.close()


class _PageExtractor:
    def extract(self, html: str, url: str) -> str | None:
        if "Полный текст страницы" not in html:
            return None
        return "Полный текст страницы. " * 40


def test_short_announcement_fetches_article_and_long_rss_body_does_not(tmp_path):
    catalog = """
sources:
  - id: alpha
    name: Альфа
    type: test
    site: https://alpha.example
    status: ok
    rss:
      - url: https://alpha.example/rss.xml
        check: ok
"""
    settings = settings_for(tmp_path, catalog, fetch_fulltext=True)
    session = open_session(settings)
    short = _item(
        "s",
        "https://alpha.example/a/short",
        "Коротко",
        "Sun, 22 Nov 2026 18:00:00 +0300",
        "Только анонс.",
    )
    long = _item(
        "l",
        "https://alpha.example/a/long",
        "Длинно",
        "Sun, 22 Nov 2026 19:00:00 +0300",
        LONG,
    )
    page_hits = {"short": 0, "long": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rss.xml":
            return httpx.Response(200, content=_rss(short, long))
        if path == "/a/short":
            page_hits["short"] += 1
            html = "<html><body><article><p>Полный текст страницы. Подробности.</p></article></body></html>"
            return httpx.Response(200, text=html)
        if path == "/a/long":
            page_hits["long"] += 1
            return httpx.Response(200, text="should not be fetched")
        return httpx.Response(404)

    world = _World()
    world.handler = handler
    try:
        stats = _collect(settings, session, world, extractor=_PageExtractor())
        assert stats.items_new == 2
        assert page_hits == {"short": 1, "long": 0}
        rows = {row.guid: row for row in session.query(Article)}
        assert rows["s"].content_origin == "extracted"
        assert rows["s"].text.startswith("Полный текст страницы.")
        assert rows["l"].content_origin == "rss"
        assert "Подробный текст" in rows["l"].text
    finally:
        session.close()


def test_pipeline_stubs_leave_new_articles_pending(tmp_path):
    catalog = """
sources:
  - id: alpha
    name: Альфа
    type: test
    site: https://alpha.example
    status: ok
    rss:
      - url: https://alpha.example/rss.xml
        check: ok
"""
    settings = settings_for(tmp_path, catalog, pipeline_enabled=True)
    session = open_session(settings)
    world = _World()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_rss(
                _item(
                    "1",
                    "https://alpha.example/news/1",
                    "Новость",
                    "Sun, 22 Nov 2026 18:00:00 +0300",
                    LONG,
                )
            ),
        )

    world.handler = handler
    try:
        stats = _collect(settings, session, world)
        assert stats.items_new == 1
        article = session.query(Article).one()
        assert article.pipeline_status == "pending"
        assert article.category is None
        assert article.sentiment is None
        assert article.fake_score is None
    finally:
        session.close()
