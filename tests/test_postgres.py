import os
from datetime import datetime, timezone

import pytest

from fast_parser.db import init_db
from fast_parser.models import Article, Source
from fast_parser.timeutil import utcnow
from fast_parser.web.queries import ArticleFilters, search_articles

URL = os.environ.get("TEST_POSTGRES_URL", "postgresql+psycopg://fast:fast@127.0.0.1:5432/fast_parser_test")


def _available() -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    raw = URL.replace("postgresql+psycopg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(raw, connect_timeout=3) as connection:
            connection.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _available(), reason="PostgreSQL is not running")


def test_partition_fts_and_snippet():
    import psycopg

    admin = URL.replace("postgresql+psycopg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"
    name = URL.rsplit("/", 1)[1]
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{name}"')
    factory = init_db(URL)
    session = factory()
    now = utcnow()
    session.add(
        Source(
            id="tass",
            nid=1,
            name="ТАСС",
            type="агентство",
            site="https://tass.ru",
            status="ok",
            updated_at=now,
        )
    )
    session.add(
        Article(
            source_id="tass",
            source_nid=1,
            url="https://tass.ru/1",
            canonical_url="https://tass.ru/1",
            content_hash="abc",
            title="Бюджет региона",
            text="Депутаты обсудили бюджет на следующий год.",
            published_at=datetime(2024, 5, 2, tzinfo=timezone.utc),
            fetched_at=now,
            updated_at=now,
            language="ru",
            category="Экономика",
        )
    )
    session.commit()
    rows, total = search_articles(session, ArticleFilters(text="бюджет -инфляция"))
    assert total == 1
    assert rows[0][0].title == "Бюджет региона"
    vector = session.execute(
        __import__("sqlalchemy").text("SELECT search_vector IS NOT NULL FROM articles")
    ).scalar()
    assert vector is True
    session.close()
