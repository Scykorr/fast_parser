from sqlalchemy import inspect, text

from fast_parser.catalog import (
    FEDERAL_SUBJECT_CODES,
    load_catalog,
    load_merged_catalog,
    missing_subject_codes,
)
from fast_parser.cli import format_stats
from fast_parser.config import load_settings
from fast_parser.db import init_db, make_engine
from fast_parser.models import Feed, Source
from fast_parser.rss.parser import ParsedItem
from fast_parser.service import open_session
from fast_parser.storage import list_articles, snapshot, upsert_article

from conftest import ROOT, settings_for

import pytest


def test_regional_catalog_covers_every_federal_subject():
    assert len(FEDERAL_SUBJECT_CODES) == 89
    catalog = load_catalog(ROOT / "config" / "sources_regional.yaml")
    assert len(catalog.sources) == 129
    assert missing_subject_codes(catalog) == []
    by_code: dict[str, int] = {}
    for source in catalog.sources:
        assert source.region_code in FEDERAL_SUBJECT_CODES
        assert source.region
        assert source.federal_district
        by_code[source.region_code] = by_code.get(source.region_code, 0) + 1
    assert set(by_code) == FEDERAL_SUBJECT_CODES
    assert all(count >= 1 for count in by_code.values())

    collected = []
    telegram_only = []
    for source in catalog.sources:
        enabled = [feed for feed in source.rss if feed.check == "ok"]
        if source.status == "telegram_only":
            assert source.rss == []
            assert source.telegram is not None
            telegram_only.append(source.id)
        collected.extend(enabled)
    assert len(collected) == 26
    assert len(telegram_only) == 103
    assert "gov_adygea" in telegram_only


def test_merged_catalog_keeps_federal_sources_without_a_region(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'regions.sqlite').as_posix()}")
    settings = load_settings(ROOT / "config" / "settings.yaml")
    merged = load_merged_catalog([settings.sources_path, settings.regional_sources_path])
    assert len(merged.sources) == 161
    by_id = {source.id: source for source in merged.sources}
    assert by_id["tass"].region_code is None
    assert by_id["gov_adygea"].region_code == "01"
    assert by_id["gov_adygea"].region == "Республика Адыгея"
    assert by_id["gov_adygea"].federal_district == "Южный"
    assert by_id["gtrk_karelia"].rss[0].url == "https://tv-karelia.ru/feed/"

    session = open_session(settings)
    try:
        stats = snapshot(session)
        text_stats = format_stats(stats)
        adygea = session.get(Source, "gov_adygea")
        feed = session.query(Feed).filter(Feed.url == "https://tv-karelia.ru/feed/").one()
        assert adygea is not None
        assert adygea.region_code == "01"
        assert adygea.telegram_username == "@muratkumpilov"
        assert feed.enabled is True
        assert feed.source_id == "gtrk_karelia"
    finally:
        session.close()

    assert stats.regions_covered == 89
    assert len(stats.by_district) == 8
    assert sum(item.regions for item in stats.by_district) == 89
    assert "По федеральным округам:" in text_stats
    assert "Южный: регионов" in text_stats
    assert "01  Республика Адыгея (Южный): источников" in text_stats


def test_articles_can_be_filtered_by_region_and_district(tmp_path):
    federal = """
sources:
  - id: tass
    name: ТАСС
    type: федеральное_агентство
    site: https://tass.ru
    status: ok
    rss: []
"""
    regional = """
sources:
  - id: gov_adygea
    name: Адыгея
    type: орган_власти
    site: https://www.adygheya.ru
    status: ok
    region: Республика Адыгея
    region_code: "01"
    federal_district: Южный
    rss:
      - url: https://adygea.example/rss
        check: ok
  - id: gov_karelia
    name: Карелия
    type: орган_власти
    site: https://gov.karelia.ru
    status: ok
    region: Республика Карелия
    region_code: 7
    federal_district: Северо-Западный
    rss:
      - url: https://karelia.example/rss
        check: ok
"""
    settings = settings_for(tmp_path, federal)
    (tmp_path / "regional.yaml").write_text(regional, encoding="utf-8")
    settings.regional_sources_path = tmp_path / "regional.yaml"
    session = open_session(settings)
    try:
        adygea_feed = session.query(Feed).filter(Feed.source_id == "gov_adygea").one()
        karelia_feed = session.query(Feed).filter(Feed.source_id == "gov_karelia").one()
        upsert_article(
            session,
            source_id="gov_adygea",
            feed_id=adygea_feed.id,
            item=_item("https://adygea.example/1", "Новость Адыгеи"),
            language="ru",
        )
        upsert_article(
            session,
            source_id="gov_karelia",
            feed_id=karelia_feed.id,
            item=_item("https://karelia.example/1", "Новость Карелии"),
            language="ru",
        )
        session.commit()
        adygea = list_articles(session, region_code="01")
        south = list_articles(session, federal_district="Южный")
        northwest = list_articles(session, federal_district="Северо-Западный")
        stats = snapshot(session)
        stored_karelia = session.get(Source, "gov_karelia")
    finally:
        session.close()

    assert stored_karelia.region_code == "07"
    assert [article.title for article in adygea] == ["Новость Адыгеи"]
    assert [article.title for article in south] == ["Новость Адыгеи"]
    assert [article.title for article in northwest] == ["Новость Карелии"]
    south_stat = next(item for item in stats.by_district if item.federal_district == "Южный")
    assert south_stat.articles == 1
    assert south_stat.sources == 1
    adygea_stat = next(item for item in stats.by_region if item.region_code == "01")
    assert adygea_stat.articles == 1


def test_duplicate_ids_across_catalogs_are_rejected(tmp_path):
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    body = """
sources:
  - id: same
    name: Один
    type: test
    site: https://example.com
    status: ok
    rss: []
"""
    first.write_text(body, encoding="utf-8")
    second.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="same"):
        load_merged_catalog([first, second])


def test_existing_sqlite_gains_region_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    url = f"sqlite:///{path.as_posix()}"
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sources (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    type VARCHAR(64) NOT NULL,
                    site VARCHAR(512) NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    notes TEXT,
                    telegram_username VARCHAR(128),
                    telegram_title VARCHAR(255),
                    telegram_check VARCHAR(32),
                    telegram_meta JSON,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
    engine.dispose()

    factory = init_db(url)
    upgraded = inspect(make_engine(url))
    columns = {column["name"] for column in upgraded.get_columns("sources")}
    assert {"region", "region_code", "federal_district"} <= columns

    session = factory()
    try:
        from fast_parser.timeutil import utcnow

        session.add(
            Source(
                id="tass",
                name="ТАСС",
                type="федеральное_агентство",
                site="https://tass.ru",
                status="ok",
                updated_at=utcnow(),
            )
        )
        session.commit()
        stored = session.get(Source, "tass")
        assert stored.region is None
        assert stored.region_code is None
        assert stored.federal_district is None
    finally:
        session.close()


def _item(url: str, title: str) -> ParsedItem:
    return ParsedItem(
        guid=url,
        url=url,
        title=title,
        summary=title,
        text=title,
        authors=[],
        published_at=None,
    )
