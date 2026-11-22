from datetime import datetime, timezone

from fast_parser.models import Article
from fast_parser.rss.parser import ParsedItem
from fast_parser.storage import upsert_article
from fast_parser.telegram.collect import collect_telegram
from fast_parser.telegram.posts import acceptable
from fast_parser.telegram.preview import parse_preview

from conftest import settings_for

HTML = """
<title>ТАСС – Telegram</title>
<div class="tgme_channel_info_header_title"><span>ТАСС</span></div>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="tass_agency/10">
    <div class="tgme_widget_message_text js-message_text">Коротко</div>
    <span class="tgme_widget_message_views">12</span>
    <time datetime="2026-11-22T10:00:00+00:00"></time>
  </div>
</div>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="tass_agency/11">
    <div class="tgme_widget_message_text js-message_text">#реклама Купите наш курс уже сегодня по промокоду</div>
    <time datetime="2026-11-22T11:00:00+00:00"></time>
  </div>
</div>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="tass_agency/12">
    <div class="tgme_widget_message_text js-message_text">Центробанк повысил ключевую ставку из-за роста цен.<br/>Подробности на <a href="https://tass.ru/ekonomika/1">сайте</a>.</div>
    <span class="tgme_widget_message_views">1.5K</span>
    <time datetime="2026-11-22T12:00:00+00:00"></time>
  </div>
</div>
"""


def test_preview_keeps_a_news_post_and_marks_a_missing_channel():
    channel = parse_preview("tass_agency", HTML)
    assert channel.alive
    assert channel.title == "ТАСС"
    assert [post.message_id for post in channel.posts] == [10, 11, 12]
    news = channel.posts[-1]
    assert news.views == 1500
    assert news.links == ["https://tass.ru/ekonomika/1"]
    assert "ключевую ставку" in news.text
    assert not acceptable(channel.posts[0].text)
    assert not acceptable(channel.posts[1].text)
    assert acceptable(news.text)
    missing = parse_preview("gone", "<title>Telegram: Contact @gone</title>")
    assert missing.alive is False
    assert missing.posts == []


def test_collect_catches_up_and_dedups_an_rss_link(tmp_path, monkeypatch):
    settings = settings_for(
        tmp_path,
        """
sources:
  - id: tass
    name: ТАСС
    type: федеральное_агентство
    role: media
    site: https://tass.ru
    status: ok
    telegram:
      username: "@tass_agency"
      title: ТАСС
      check: ok
    rss: []
""",
    )
    settings.telegram.enabled = True
    settings.telegram.pause_seconds = 0
    from fast_parser.service import open_session

    session = open_session(settings)
    upsert_article(
        session,
        source_id="tass",
        feed_id=None,
        item=ParsedItem(
            guid="rss-1",
            url="https://tass.ru/ekonomika/1",
            title="Ставка",
            summary="",
            text="Полный текст rss про ставку и инфляцию в релизе банка.",
            authors=[],
            published_at=datetime(2026, 11, 22, tzinfo=timezone.utc),
            origin="rss",
        ),
        language="ru",
    )
    session.commit()

    def fake_fetch(username, *, last_id, limit, use_mtproto, session_file):
        assert use_mtproto is False
        assert last_id == 0
        return [
            {
                "message_id": 12,
                "text": "Центробанк повысил ключевую ставку из-за роста цен. Подробности уже в ленте.",
                "published_at": datetime(2026, 11, 22, 12, tzinfo=timezone.utc),
                "views": 1500,
                "forwards": 3,
                "links": ["https://tass.ru/ekonomika/1"],
            },
            {
                "message_id": 13,
                "text": "В регионе открыли новую школу и пригласили жителей на линейку во дворе.",
                "published_at": datetime(2026, 11, 22, 13, tzinfo=timezone.utc),
                "views": 20,
                "forwards": None,
                "links": [],
            },
        ]

    monkeypatch.setattr("fast_parser.telegram.collect._fetch", fake_fetch)
    stats = collect_telegram(settings, session, pause_seconds=0)
    assert stats.mode == "preview"
    assert stats.new == 1
    assert stats.duplicate == 1
    stored = session.query(Article).filter(Article.content_origin == "telegram").one()
    assert stored.canonical_url == "https://t.me/tass_agency/13"
    assert stored.extra["views"] == 20
    session.close()


def test_mtproto_module_does_not_join_or_send():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "fast_parser" / "telegram" / "mtproto.py").read_text(
        encoding="utf-8"
    )
    assert "JoinChannelRequest" not in source
    assert "send_message" not in source


def test_login_without_keys_explains_where_to_put_them(monkeypatch):
    from fast_parser.cli import main

    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.chdir("/tmp")
    code = main(["telegram", "login"])
    assert code == 2
