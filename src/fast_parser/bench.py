"""Bytes per article and typical search latency on a synthetic corpus."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

WORDS = (
    "бюджет ставка инфляция метро пожар школа больница спорт культура "
    "губернатор договор урожай паводок выборы наука завод дорога"
).split()


def measure(*, rows: int, postgres_url: str, workdir: Path) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    sqlite_path = workdir / "bench.sqlite"
    if sqlite_path.exists():
        sqlite_path.unlink()
    _prepare_postgres(postgres_url)
    sqlite_seconds = _load_sqlite(sqlite_path, rows)
    postgres_seconds = _load_postgres(postgres_url, rows)
    parquet = workdir / "bench.parquet"
    parquet_bytes = _write_parquet(postgres_url, parquet, rows)
    sqlite_bytes = sqlite_path.stat().st_size
    postgres_bytes = _postgres_bytes(
        postgres_url,
        """
        SELECT COALESCE(sum(pg_total_relation_size(inhrelid)), 0)
        FROM pg_inherits
        WHERE inhparent = 'articles'::regclass
        """,
    )
    database_bytes = _postgres_bytes(postgres_url, "SELECT pg_database_size(current_database())")
    timings = _timings(postgres_url)
    lines = [
        f"Статей: {rows}",
        f"SQLite: {sqlite_bytes / rows:.1f} байт/статья, файл {sqlite_bytes / 1_048_576:.1f} МиБ, загрузка {sqlite_seconds:.1f} с",
        f"PostgreSQL: {postgres_bytes / rows:.1f} байт/статья, таблица {postgres_bytes / 1_048_576:.1f} МиБ, база {database_bytes / 1_048_576:.1f} МиБ, загрузка {postgres_seconds:.1f} с",
        f"Parquet zstd: {parquet_bytes / rows:.1f} байт/статья, файл {parquet_bytes / 1_048_576:.1f} МиБ",
        "Запросы PostgreSQL:",
    ]
    for name, seconds, count in timings:
        lines.append(f"  {name}: {seconds * 1000:.1f} мс, строк {count}")
    return "\n".join(lines)


def _rows(count: int):
    start = datetime(2024, 1, 1)
    for index in range(count):
        published = start + timedelta(minutes=index % (60 * 24 * 400))
        word = WORDS[index % len(WORDS)]
        title = f"Новость {index} {word}"
        text = (
            f"{title}. Региональное издание сообщает, что {word} остаётся темой дня. "
            "Читатели обсуждают последствия и ждут комментария властей. "
            "Корреспондент побывал на месте и записал реакцию жителей. "
            f"Номер материала {index}."
        )
        yield (
            index + 1,
            f"src-{index % 40}",
            f"https://example.test/{index}",
            f"hash-{index}",
            title,
            text,
            published,
            "ru",
            "Происшествия" if index % 5 == 0 else "Экономика",
            "negative" if index % 3 == 0 else "neutral",
        )


def _load_sqlite(path: Path, count: int) -> float:
    from fast_parser.db import init_db
    from fast_parser.models import Source
    from fast_parser.timeutil import utcnow

    factory = init_db(f"sqlite:///{path.as_posix()}")
    session = factory()
    now = utcnow()
    for source_id in range(40):
        session.add(
            Source(
                id=f"src-{source_id}",
                nid=source_id + 1,
                name=f"Источник {source_id}",
                type="газета",
                site="https://example.test",
                status="ok",
                region_code=f"{source_id:02d}",
                region="Регион",
                federal_district="Центральный",
                updated_at=now,
            )
        )
    session.commit()
    started = time.perf_counter()
    batch = []
    for row in _rows(count):
        batch.append(row)
        if len(batch) == 5000:
            _insert_sqlite(session, batch, now)
            batch.clear()
    if batch:
        _insert_sqlite(session, batch, now)
    session.close()
    return time.perf_counter() - started


def _insert_sqlite(session, batch, now) -> None:
    from fast_parser.models import Article

    session.add_all(
        [
            Article(
                source_id=source_id,
                url=url,
                canonical_url=url,
                content_hash=digest,
                title=title,
                text=body,
                published_at=published,
                fetched_at=now,
                updated_at=now,
                language=language,
                category=category,
                sentiment=sentiment,
            )
            for _id, source_id, url, digest, title, body, published, language, category, sentiment in batch
        ]
    )
    session.commit()


def _prepare_postgres(url: str) -> None:
    import psycopg

    admin = url.rsplit("/", 1)[0] + "/postgres"
    raw = admin.replace("postgresql+psycopg://", "postgresql://")
    name = url.rsplit("/", 1)[1]
    with psycopg.connect(raw, autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{name}"')
    from fast_parser.db import init_db
    from fast_parser.models import Source
    from fast_parser.timeutil import utcnow

    factory = init_db(url)
    session = factory()
    now = utcnow()
    for source_id in range(40):
        session.add(
            Source(
                id=f"src-{source_id}",
                nid=source_id + 1,
                name=f"Источник {source_id}",
                type="газета",
                site="https://example.test",
                status="ok",
                region_code=f"{source_id:02d}",
                region="Регион",
                federal_district="Центральный",
                updated_at=now,
            )
        )
    session.commit()
    session.close()


def _load_postgres(url: str, count: int) -> float:
    import psycopg

    raw = url.replace("postgresql+psycopg://", "postgresql://")
    started = time.perf_counter()
    with psycopg.connect(raw) as connection:
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE articles DISABLE TRIGGER articles_search_vector_trg")
            batch = []
            for row in _rows(count):
                batch.append(row)
                if len(batch) == 5000:
                    cursor.executemany(
                        """
                        INSERT INTO articles (
                            id, source_id, url, canonical_url, content_hash, title, text,
                            published_at, fetched_at, updated_at, language, category, sentiment
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (index, source, url, url, digest, title, body, published, published, published, language, category, sentiment)
                            for index, source, url, digest, title, body, published, language, category, sentiment in batch
                        ],
                    )
                    connection.commit()
                    batch.clear()
            if batch:
                cursor.executemany(
                    """
                    INSERT INTO articles (
                        id, source_id, url, canonical_url, content_hash, title, text,
                        published_at, fetched_at, updated_at, language, category, sentiment
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    [
                        (index, source, url, url, digest, title, body, published, published, published, language, category, sentiment)
                        for index, source, url, digest, title, body, published, language, category, sentiment in batch
                    ],
                )
            cursor.execute(
                """
                UPDATE articles SET search_vector =
                    setweight(to_tsvector('russian', coalesce(title, '')), 'A') ||
                    setweight(to_tsvector('russian', coalesce(text, '')), 'B')
                """
            )
            cursor.execute("ALTER TABLE articles ENABLE TRIGGER articles_search_vector_trg")
            cursor.execute("ANALYZE articles")
        connection.commit()
    return time.perf_counter() - started


def _write_parquet(url: str, path: Path, count: int) -> int:
    import duckdb

    dsn = url.replace("postgresql+psycopg://", "postgres://")
    connection = duckdb.connect()
    connection.execute("INSTALL postgres; LOAD postgres;")
    connection.execute(f"ATTACH '{dsn}' AS pg (TYPE POSTGRES, READ_ONLY)")
    connection.execute(
        f"COPY (SELECT * FROM pg.articles) TO '{path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    connection.close()
    return path.stat().st_size


def _postgres_bytes(url: str, sql: str) -> int:
    import psycopg

    raw = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(raw) as connection:
        return int(connection.execute(sql).fetchone()[0])


def _timings(url: str) -> list[tuple[str, float, int]]:
    import psycopg

    raw = url.replace("postgresql+psycopg://", "postgresql://")
    queries = [
        ("диапазон 7 дней", "SELECT count(*) FROM articles WHERE published_at >= TIMESTAMP '2024-06-01' AND published_at < TIMESTAMP '2024-06-08'"),
        ("источник и рубрика", "SELECT count(*) FROM articles WHERE source_id = 'src-3' AND category = 'Экономика'"),
        ("полнотекст, сколько совпало", "SELECT count(*) FROM articles WHERE search_vector @@ websearch_to_tsquery('russian', 'бюджет')"),
        (
            "лента: неделя и слово, 50 строк",
            """
            SELECT id FROM articles
            WHERE published_at >= TIMESTAMP '2024-06-01' AND published_at < TIMESTAMP '2024-06-08'
              AND search_vector @@ websearch_to_tsquery('russian', 'бюджет')
            ORDER BY published_at DESC
            LIMIT 50
            """,
        ),
    ]
    found = []
    with psycopg.connect(raw) as connection:
        for name, sql in queries:
            connection.execute(sql).fetchall()
            started = time.perf_counter()
            rows = connection.execute(sql).fetchall()
            count = rows[0][0] if len(rows) == 1 else len(rows)
            found.append((name, time.perf_counter() - started, int(count)))
    return found
