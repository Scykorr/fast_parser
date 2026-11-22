from fast_parser.catalog import feed_is_collected, load_catalog
from fast_parser.config import load_settings
from fast_parser.service import open_session
from fast_parser.storage import snapshot

from conftest import ROOT


def test_real_catalog_keeps_every_source_and_skips_dead_feeds(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'catalog.sqlite').as_posix()}")
    settings = load_settings(ROOT / "config" / "settings.yaml")
    catalog = load_catalog(settings.sources_path)
    assert len(catalog.sources) == 32

    enabled: list[tuple[str, str]] = []
    disabled_checks: set[str] = set()
    skip_urls = set(settings.collector.skip_feed_urls)
    for source in catalog.sources:
        for feed in source.rss:
            if feed_is_collected(
                feed,
                source_id=source.id,
                skip_feed_urls=skip_urls,
                skip_source_ids=set(settings.collector.skip_source_ids),
            ):
                enabled.append((source.id, feed.url))
            else:
                disabled_checks.add(feed.check or "")

    enabled_urls = {url for _, url in enabled}
    assert len(enabled) == 25
    assert ("tass", "https://tass.ru/rss/v2.xml") in [(sid, url) for sid, url in enabled]
    assert "http://kremlin.ru/events/all/feed" in enabled_urls
    assert "http://government.ru/news/rss/" in enabled_urls
    assert "http://redstar.ru/feed/" in enabled_urls
    assert "https://www.cbr.ru/rss/RssCurrency" not in enabled_urls
    assert "https://www.cbr.ru/rss/RssPress" in enabled_urls
    assert "https://mid.ru/ru/rss.php" not in enabled_urls
    assert disabled_checks <= {"empty", "antibot", "unverified", "ok"}
    # The only `ok` feed we deliberately do not collect is the FX fixing.
    assert "https://www.cbr.ru/rss/RssCurrency" in skip_urls

    by_id = {source.id: source for source in catalog.sources}
    assert by_id["tv1"].rss == []
    assert by_id["tv1"].telegram is not None
    assert by_id["tv1"].telegram.username == "@news_1tv"
    assert by_id["vesti"].telegram is not None
    assert by_id["vesti"].telegram.username == "@vestiru24"
    assert by_id["vesti"].telegram.aliases == ["@vestiru"]
    assert by_id["mid"].telegram.username == "@MID_Russia"
    assert by_id["sputnik_ru"].status == "empty_feed"

    session = open_session(settings)
    try:
        stats = snapshot(session)
    finally:
        session.close()
    assert stats.sources == 195
    assert stats.federal_sources == 32
    assert stats.regional_sources == 163
    assert stats.regions_covered == 89
    assert stats.feeds_enabled == 51
    assert stats.telegram >= 20
    assert stats.articles == 0
