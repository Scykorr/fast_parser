from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fast_parser.models import Base


def make_engine(url: str) -> Engine:
    connect_args: dict = {}
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        _ensure_sqlite_parent(url)
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, connect_args=connect_args, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _connection_record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            dbapi_conn.create_function(
                "py_lower",
                1,
                lambda value: value.casefold() if isinstance(value, str) else value,
            )

    return engine


# Nullable columns added after the first SQLite file may already exist.
# create_all does not alter an existing table.
_SOURCE_COLUMN_UPGRADES = (
    ("region", "VARCHAR(128)"),
    ("region_code", "VARCHAR(8)"),
    ("federal_district", "VARCHAR(64)"),
    ("nid", "INTEGER"),
)
_ARTICLE_COLUMN_UPGRADES = (
    ("source_nid", "INTEGER"),
    ("region_id", "INTEGER"),
    ("district_id", "INTEGER"),
    ("category_id", "INTEGER"),
    ("embedding", "BLOB"),
    ("category_confidence", "FLOAT"),
    ("fake_label", "VARCHAR(64)"),
    ("domain", "VARCHAR(255)"),
    ("text_length", "INTEGER"),
    ("tags", "JSON"),
)


def init_db(url: str) -> sessionmaker[Session]:
    engine = make_engine(url)
    if engine.dialect.name == "postgresql":
        from fast_parser.pg_schema import ensure_postgres

        ensure_postgres(engine)
    else:
        Base.metadata.create_all(engine)
        _upgrade_schema(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _upgrade_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    if "sources" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("sources")}
    with engine.begin() as conn:
        for name, ddl in _SOURCE_COLUMN_UPGRADES:
            if name not in present:
                conn.execute(text(f"ALTER TABLE sources ADD COLUMN {name} {ddl}"))
        if "articles" in inspector.get_table_names():
            article_cols = {column["name"] for column in inspector.get_columns("articles")}
            added_article = False
            for name, ddl in _ARTICLE_COLUMN_UPGRADES:
                if name not in article_cols:
                    conn.execute(text(f"ALTER TABLE articles ADD COLUMN {name} {ddl}"))
                    added_article = True
            if added_article:
                conn.execute(
                    text(
                        """
                        UPDATE articles
                        SET text_length = length(text)
                        WHERE text_length IS NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        UPDATE articles
                        SET category_confidence = json_extract(extra, '$.category_confidence')
                        WHERE category_confidence IS NULL
                          AND json_extract(extra, '$.category_confidence') IS NOT NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        UPDATE articles
                        SET tags = json_extract(extra, '$.categories')
                        WHERE tags IS NULL
                          AND json_type(extra, '$.categories') = 'array'
                        """
                    )
                )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sources_region_code ON sources (region_code)"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_sources_federal_district "
                "ON sources (federal_district)"
            )
        )


def _ensure_sqlite_parent(url: str) -> None:
    if ":memory:" in url:
        return
    marker = "sqlite:///"
    if marker not in url:
        return
    raw = url.split(marker, 1)[1]
    if not raw or raw == ":memory:":
        return
    path = Path(raw)
    if path.parent and str(path.parent) not in {"", "."}:
        path.parent.mkdir(parents=True, exist_ok=True)
