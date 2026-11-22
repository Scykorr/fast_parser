"""Export old articles to ZSTD Parquet and, on PostgreSQL, drop those months."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_parser.models import Article, Source


def export_before(
    session: Session,
    *,
    before: date,
    out_dir: Path,
    drop: bool = False,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = session.execute(
        select(
            Article.id,
            Article.source_id,
            Source.name,
            Source.region_code,
            Source.federal_district,
            Article.title,
            Article.text,
            Article.canonical_url,
            Article.published_at,
            Article.category,
            Article.sentiment,
            Article.sentiment_score,
            Article.fake_score,
            Article.language,
            Article.content_hash,
        )
        .join(Source, Article.source_id == Source.id)
        .where(Article.published_at.is_not(None), Article.published_at < datetime.combine(before, datetime.min.time()))
    ).all()
    by_month: dict[str, list] = {}
    for row in rows:
        stamp = row.published_at.strftime("%Y-%m")
        by_month.setdefault(stamp, []).append(row)
    written: list[Path] = []
    connection = duckdb.connect()
    try:
        for stamp, month_rows in sorted(by_month.items()):
            path = out_dir / f"articles-{stamp}.parquet"
            _write_parquet(connection, month_rows, path)
            written.append(path)
    finally:
        connection.close()
    if drop and session.get_bind().dialect.name == "postgresql":
        _drop_partitions(session, before)
    elif drop:
        from sqlalchemy import delete

        session.execute(
            delete(Article).where(
                Article.published_at.is_not(None),
                Article.published_at < datetime.combine(before, datetime.min.time()),
            )
        )
        session.commit()
    return written


def _write_parquet(connection, rows, path: Path) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE export_batch (
            id BIGINT, source_id VARCHAR, source_name VARCHAR, region_code VARCHAR,
            federal_district VARCHAR, title VARCHAR, text VARCHAR, url VARCHAR,
            published_at TIMESTAMP, category VARCHAR, sentiment VARCHAR,
            sentiment_score DOUBLE, fake_score DOUBLE, language VARCHAR, content_hash VARCHAR
        )
        """
    )
    connection.executemany(
        "INSERT INTO export_batch VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                row.id,
                row.source_id,
                row.name,
                row.region_code,
                row.federal_district,
                row.title,
                row.text,
                row.canonical_url,
                row.published_at.replace(tzinfo=None) if row.published_at else None,
                row.category,
                row.sentiment,
                row.sentiment_score,
                row.fake_score,
                row.language,
                row.content_hash,
            )
            for row in rows
        ],
    )
    connection.execute(
        f"COPY export_batch TO '{path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def _drop_partitions(session: Session, before: date) -> None:
    from sqlalchemy import text

    names = session.execute(
        text(
            """
            SELECT inhrelid::regclass::text
            FROM pg_inherits
            WHERE inhparent = 'articles'::regclass
            """
        )
    ).scalars()
    for name in names:
        if not name.startswith("articles_20"):
            continue
        stamp = name.removeprefix("articles_")
        year, month = int(stamp[:4]), int(stamp[5:7])
        if date(year, month, 1) < before.replace(day=1):
            session.execute(text(f"ALTER TABLE articles DETACH PARTITION {name}"))
            session.execute(text(f"DROP TABLE {name}"))
    session.commit()


def archive_stats(directory: Path) -> list[dict]:
    files = sorted(directory.glob("*.parquet"))
    if not files:
        return []
    pattern = (directory / "*.parquet").as_posix()
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            f"""
            SELECT coalesce(category, '—') AS category, count(*) AS articles
            FROM read_parquet('{pattern}')
            GROUP BY 1
            ORDER BY articles DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return [{"category": row[0], "articles": int(row[1])} for row in rows]
