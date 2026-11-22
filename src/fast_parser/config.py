from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = "sqlite:///data/fast_parser.db"


class HttpSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    accept: str = "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8"
    accept_language: str = "ru-RU,ru;q=0.9,en;q=0.5"
    timeout_seconds: float = 25.0
    fulltext_timeout_seconds: float = 25.0
    max_concurrency: int = 4
    per_host_concurrency: int = 2
    retries: int = 2
    backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    trust_env: bool = True


class CollectorSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fetch_fulltext: bool = True
    fulltext_min_chars: int = 500
    max_feed_bytes: int = 5_000_000
    max_article_bytes: int = 2_000_000
    skip_feed_urls: list[str] = Field(default_factory=list)
    skip_source_ids: list[str] = Field(default_factory=list)


class DateSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    naive_timezone: str = "Europe/Moscow"


class PipelineSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    processors: list[str] = Field(
        default_factory=lambda: ["category", "sentiment", "fake_score"]
    )


class LoopSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    interval_seconds: int = 900


class LoggingSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    level: str = "INFO"


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sources_file: str = "sources.yaml"
    regional_sources_file: str | None = None
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    collector: CollectorSettings = Field(default_factory=CollectorSettings)
    dates: DateSettings = Field(default_factory=DateSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    loop: LoopSettings = Field(default_factory=LoopSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    sources_path: Path = Path("config/sources.yaml")
    regional_sources_path: Path | None = None
    config_path: Path = Path("config/settings.yaml")


def project_config_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    env = os.environ.get("FAST_PARSER_CONFIG")
    if env:
        return Path(env)
    cwd = Path.cwd() / "config" / "settings.yaml"
    if cwd.exists():
        return cwd
    packaged = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    return packaged


def load_settings(path: Path | None = None) -> AppSettings:
    config_path = project_config_path(path)
    raw: dict = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if loaded:
            if not isinstance(loaded, dict):
                raise ValueError(f"Конфиг должен быть словарём YAML: {config_path}")
            raw = loaded
    settings = AppSettings.model_validate(raw)
    settings.config_path = config_path

    sources_override = os.environ.get("FAST_PARSER_SOURCES")
    if sources_override:
        settings.sources_path = Path(sources_override)
    else:
        sources = Path(settings.sources_file)
        if not sources.is_absolute():
            base = config_path.parent if config_path.exists() else Path.cwd()
            sources = base / sources
        settings.sources_path = sources

    regional_override = os.environ.get("FAST_PARSER_REGIONAL_SOURCES")
    if regional_override:
        settings.regional_sources_path = Path(regional_override)
    elif settings.regional_sources_file:
        regional = Path(settings.regional_sources_file)
        if not regional.is_absolute():
            base = config_path.parent if config_path.exists() else Path.cwd()
            regional = base / regional
        settings.regional_sources_path = regional
    else:
        settings.regional_sources_path = None

    if url := os.environ.get("DATABASE_URL"):
        settings.database.url = url
    if level := os.environ.get("LOG_LEVEL"):
        settings.logging.level = level
    if interval := os.environ.get("FAST_PARSER_INTERVAL"):
        settings.loop.interval_seconds = int(interval)
    if fulltext := os.environ.get("FAST_PARSER_FETCH_FULLTEXT"):
        settings.collector.fetch_fulltext = _as_bool(fulltext)
    return settings


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
