from __future__ import annotations

from pathlib import Path

import pytest

from fast_parser.config import load_settings
from fast_parser.db import init_db
from fast_parser.models import Feed, Source
from fast_parser.timeutil import utcnow

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def session():
    factory = init_db("sqlite://")
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def source_feed(session):
    session.add(
        Source(
            id="alpha",
            name="Альфа",
            type="test",
            site="https://alpha.example",
            status="ok",
            updated_at=utcnow(),
        )
    )
    session.add(Feed(source_id="alpha", url="https://alpha.example/rss.xml", enabled=True))
    session.commit()
    feed = session.query(Feed).one()
    return session, feed


def write_settings(
    directory: Path,
    *,
    sources_name: str = "sources.yaml",
    fetch_fulltext: bool = False,
    pipeline_enabled: bool = False,
) -> Path:
    db_path = (directory / "parser.sqlite").as_posix()
    path = directory / "settings.yaml"
    fulltext = "true" if fetch_fulltext else "false"
    pipeline = "true" if pipeline_enabled else "false"
    path.write_text(
        f"""
sources_file: {sources_name}
database:
  url: sqlite:///{db_path}
http:
  retries: 1
  backoff_seconds: 0
  backoff_multiplier: 1
  timeout_seconds: 5
  fulltext_timeout_seconds: 5
  max_concurrency: 4
  per_host_concurrency: 2
collector:
  fetch_fulltext: {fulltext}
  fulltext_min_chars: 500
dates:
  naive_timezone: Europe/Moscow
pipeline:
  enabled: {pipeline}
logging:
  level: WARNING
""",
        encoding="utf-8",
    )
    return path


def settings_for(
    directory: Path,
    sources_yaml: str,
    *,
    fetch_fulltext: bool = False,
    pipeline_enabled: bool = False,
):
    (directory / "sources.yaml").write_text(sources_yaml, encoding="utf-8")
    return load_settings(
        write_settings(
            directory,
            fetch_fulltext=fetch_fulltext,
            pipeline_enabled=pipeline_enabled,
        )
    )
