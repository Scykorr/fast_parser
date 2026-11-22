from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from fast_parser import __version__
from fast_parser.config import AppSettings, load_settings
from fast_parser.logging_setup import setup_logging
from fast_parser.service import RunStats, open_session, run_once
from fast_parser.storage import StatsSnapshot, iter_export_dicts, snapshot


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        setup_logging(settings.logging.level)
        if args.command == "run":
            print(format_run(run_once(settings)))
            return 0
        if args.command == "loop":
            interval = args.interval if args.interval is not None else settings.loop.interval_seconds
            if interval < 1:
                print("Интервал должен быть не меньше 1 секунды", file=sys.stderr)
                return 2
            return run_loop(settings, interval)
        if args.command == "stats":
            print(format_stats(_stats(settings)))
            return 0
        if args.command == "export":
            count = export_jsonl(settings, args.out)
            print(f"Записано статей: {count} → {args.out}")
            return 0
        if args.command == "web":
            return run_web(settings, host=args.host, port=args.port)
        if args.command == "process":
            from fast_parser.process import process_pending

            result = process_pending(settings, limit=args.limit, all_articles=args.all)
            print(
                "Разметка завершена\n"
                f"  просмотрено {result.seen}, с рубрикой {result.with_category}, "
                f"с тональностью {result.with_sentiment}, готовы {result.completed}"
            )
            if not _ml_installed():
                print("  модель тональности не установлена: pip install -e \".[ml]\"")
            return 0
        if args.command == "search":
            from fast_parser.search_filters import parse_filters
            from fast_parser.web.queries import search_articles

            session = open_session(settings)
            try:
                filters = parse_filters(_search_raw(args), limit=args.limit)
                rows, total = search_articles(session, filters)
            finally:
                session.close()
            print(f"Найдено: {total}")
            for article, source in rows:
                stamp = article.published_at.date().isoformat() if article.published_at else "—"
                print(f"{stamp}  {source.name}  {article.title}")
            return 0
        if args.command == "archive":
            from datetime import date as date_cls

            from fast_parser.archive import export_before

            session = open_session(settings)
            try:
                written = export_before(
                    session,
                    before=date_cls.fromisoformat(args.before),
                    out_dir=args.out,
                    drop=args.drop,
                )
            finally:
                session.close()
            print(f"Файлов: {len(written)}")
            for path in written:
                print(f"  {path}")
            return 0
        if args.command == "migrate-sqlite":
            from fast_parser.migrate_sqlite import migrate_sqlite

            session = open_session(settings)
            try:
                stats = migrate_sqlite(args.sqlite, session)
            finally:
                session.close()
            print(f"Источники: {stats.sources}, ленты: {stats.feeds}, статьи: {stats.articles}")
            return 0
        if args.command == "bench":
            from fast_parser.bench import measure

            report = measure(rows=args.rows, postgres_url=args.postgres, workdir=args.out)
            print(report)
            return 0
        if args.command == "eval":
            from fast_parser.evaluate import format_report, load_labels, score_dataset

            rows = load_labels(args.set)
            keyword, _, _, _ = score_dataset(settings, rows, use_embeddings=False)
            settings.pipeline.category.use_head = False
            settings.pipeline.sentiment.use_head = False
            baseline, baseline_sentiment, _, _ = score_dataset(settings, rows, use_embeddings=True)
            settings.pipeline.category.use_head = True
            settings.pipeline.sentiment.use_head = True
            _, _, held_out_category, held_out_sentiment = score_dataset(settings, rows, use_embeddings=True)
            print(
                format_report(
                    keyword,
                    baseline if _ml_installed() else None,
                    baseline_sentiment,
                    held_out_category,
                    held_out_sentiment,
                )
            )
            return 0
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nОстановлено")
        return 130
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    parser.error(f"неизвестная команда: {args.command}")
    return 2


def run_loop(settings: AppSettings, interval: int) -> int:
    print(f"Цикл сбора каждые {interval} с. Остановка: Ctrl+C.")
    while True:
        started = time.monotonic()
        try:
            print(format_run(run_once(settings)))
        except KeyboardInterrupt:
            print("\nОстановлено")
            return 130
        except Exception:
            import logging

            logging.getLogger(__name__).exception("прогон завершился с ошибкой, следующий по расписанию")
        remaining = interval - (time.monotonic() - started)
        if remaining > 0:
            try:
                time.sleep(remaining)
            except KeyboardInterrupt:
                print("\nОстановлено")
                return 130


def run_web(settings: AppSettings, *, host: str | None, port: int | None) -> int:
    import uvicorn

    from fast_parser.web.app import create_app

    bind_host = host or settings.web.host
    bind_port = port if port is not None else settings.web.port
    print(f"Веб-интерфейс: http://{bind_host}:{bind_port}")
    uvicorn.run(create_app(settings), host=bind_host, port=bind_port, log_level="info")
    return 0


def export_jsonl(settings: AppSettings, path: Path) -> int:
    session = open_session(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w", encoding="utf-8") as handle:
            for row in iter_export_dicts(session):
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
        return count
    finally:
        session.close()


def _stats(settings: AppSettings) -> StatsSnapshot:
    session = open_session(settings)
    try:
        return snapshot(session)
    finally:
        session.close()


def format_run(stats: RunStats) -> str:
    lines = [
        "Прогон завершён",
        (
            f"  ленты: {stats.feeds_total}, успешно: {stats.feeds_ok}, "
            f"без изменений: {stats.feeds_not_modified}, пустые: {stats.feeds_empty}, "
            f"ошибки: {stats.feeds_error}"
        ),
        (
            f"  записи: получено {stats.items_fetched}, новых {stats.items_new}, "
            f"дублей {stats.items_duplicate}, обновлено {stats.items_updated}"
        ),
    ]
    if stats.errors:
        lines.append("  ошибки:")
        for url, error in stats.errors[:20]:
            lines.append(f"    - {url}: {error}")
        extra = len(stats.errors) - 20
        if extra > 0:
            lines.append(f"    … и ещё {extra}")
    return "\n".join(lines)


def format_stats(stats: StatsSnapshot) -> str:
    lines = [
        (
            f"Источники: {stats.sources} "
            f"(федеральные: {stats.federal_sources}, региональные: {stats.regional_sources}, "
            f"субъектов: {stats.regions_covered})"
        ),
        f"Ленты: {stats.feeds} (в сборе: {stats.feeds_enabled})",
        f"Telegram-каналы в каталоге: {stats.telegram}",
        f"Статьи: {stats.articles} (ожидают обработки: {stats.pending})",
        f"Последний успешный опрос ленты: {_ts(stats.last_success_at)}",
        f"Самая свежая публикация: {_ts(stats.newest_published_at)}",
    ]
    if stats.by_district:
        lines.append("По федеральным округам:")
        for item in stats.by_district:
            lines.append(
                f"  {item.federal_district}: регионов {item.regions}, "
                f"источников {item.sources}, статей {item.articles}"
            )
    if stats.by_region:
        lines.append("По регионам:")
        for item in stats.by_region:
            lines.append(
                f"  {item.region_code}  {item.region} ({item.federal_district}): "
                f"источников {item.sources}, статей {item.articles}"
            )
    if stats.recent_errors:
        lines.append("Последние ошибки лент:")
        for url, error in stats.recent_errors:
            lines.append(f"  - {url}: {error}")
    filled = [(source_id, name, count) for source_id, name, count in stats.by_source if count]
    if filled:
        lines.append("Статьи по источникам:")
        for source_id, name, count in filled:
            lines.append(f"  {count:5d}  {name} ({source_id})")
    return "\n".join(lines)


def _search_raw(args) -> dict:
    return {
        "q": args.q,
        "title_q": args.title,
        "body_q": args.body,
        "period": args.period,
        "date_on": args.date_on,
        "date_from": args.date_from,
        "date_to": args.date_to,
        "source_id": args.source or [],
        "source_id_not": args.source_not or [],
        "source_type": args.source_type or [],
        "source_type_not": args.source_type_not or [],
        "source_status": args.source_status or [],
        "official": args.official,
        "region": args.region or [],
        "region_code": args.region_code or [],
        "region_code_not": args.region_code_not or [],
        "federal_district": args.district or [],
        "federal_district_not": args.district_not or [],
        "category": args.category or [],
        "category_not": args.category_not or [],
        "sentiment": args.sentiment or [],
        "sentiment_not": args.sentiment_not or [],
        "channel": args.channel or [],
        "language": args.language or [],
        "pipeline_status": args.status or [],
        "tag": args.tag or [],
        "tag_not": args.tag_not or [],
        "author": args.author or [],
        "domain": args.domain or [],
        "url": args.url,
        "weekday": args.weekday or [],
        "hour": args.hour or [],
        "sentiment_min": args.sentiment_min,
        "sentiment_max": args.sentiment_max,
        "category_confidence_min": args.category_confidence_min,
        "category_confidence_max": args.category_confidence_max,
        "fake_score_min": args.fake_min,
        "fake_score_max": args.fake_max,
        "fake_label": args.fake_label or [],
        "has_fulltext": args.fulltext,
        "text_len_min": args.text_len_min,
        "text_len_max": args.text_len_max,
        "min_cluster_sources": args.min_sources,
        "fuzzy": "true" if args.fuzzy else None,
        "limit": args.limit,
    }


def _ts(value) -> str:
    if value is None:
        return "—"
    return value.isoformat(sep=" ", timespec="seconds")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fast-parser",
        description="Сборщик RSS российских государственных СМИ. При каждом запуске догоняет пропущенное.",
    )
    parser.add_argument("--version", action="version", version=f"fast-parser {__version__}")
    parser.add_argument("--config", type=Path, help="Путь к settings.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Один прогон: забрать всё, что сейчас есть в лентах, и отсеять дубли")
    loop = sub.add_parser("loop", help="Повторять сбор, пока процесс запущен")
    loop.add_argument("--interval", type=int, default=None, help="Секунды между прогонами")
    sub.add_parser("stats", help="Сводка по локальной базе")
    export = sub.add_parser("export", help="Выгрузить статьи в JSONL (текст и метаданные для будущего RAG)")
    export.add_argument("--out", type=Path, required=True, help="Файл .jsonl")
    web = sub.add_parser("web", help="Локальный веб-интерфейс и JSON API")
    web.add_argument("--host", default=None, help="Адрес, по умолчанию 127.0.0.1")
    web.add_argument("--port", type=int, default=None, help="Порт, по умолчанию 8080")
    process = sub.add_parser("process", help="Разметить статьи без рубрики или тональности")
    process.add_argument("--limit", type=int, default=None, help="Сколько статей взять за запуск")
    process.add_argument("--all", action="store_true", help="Пересчитать даже уже размеченные статьи")
    evaluate = sub.add_parser("eval", help="Посчитать accuracy и macro-F1 на ручной выборке")
    evaluate.add_argument("--set", type=Path, default=Path("eval/labels.jsonl"), help="Файл jsonl с полями category и sentiment")
    search = sub.add_parser("search", help="Поиск теми же фильтрами, что и API. Параметр можно повторять, --*-not исключает")
    search.add_argument("q", nargs="?", default=None, help="Запрос: фразы, OR, -исключить")
    search.add_argument("--title", help="Слова только в заголовке")
    search.add_argument("--body", help="Слова только в тексте")
    search.add_argument("--period", choices=["today", "24h", "7d", "30d", "month", "365d"])
    search.add_argument("--date-on", choices=["published", "collected"], default="published")
    search.add_argument("--date-from")
    search.add_argument("--date-to")
    search.add_argument("--source", action="append")
    search.add_argument("--source-not", action="append")
    search.add_argument("--source-type", action="append")
    search.add_argument("--source-type-not", action="append")
    search.add_argument("--source-status", action="append")
    search.add_argument("--official", choices=["yes", "no"])
    search.add_argument("--region", action="append")
    search.add_argument("--region-code", action="append")
    search.add_argument("--region-code-not", action="append")
    search.add_argument("--district", action="append")
    search.add_argument("--district-not", action="append")
    search.add_argument("--category", action="append")
    search.add_argument("--category-not", action="append")
    search.add_argument("--sentiment", action="append")
    search.add_argument("--sentiment-not", action="append")
    search.add_argument("--channel", action="append", choices=["rss", "telegram", "vk", "x"])
    search.add_argument("--language", action="append")
    search.add_argument("--status", action="append", help="pending или processed")
    search.add_argument("--tag", action="append")
    search.add_argument("--tag-not", action="append")
    search.add_argument("--author", action="append")
    search.add_argument("--domain", action="append")
    search.add_argument("--url")
    search.add_argument("--weekday", action="append", type=int, help="1=понедельник … 7=воскресенье")
    search.add_argument("--hour", action="append", type=int)
    search.add_argument("--sentiment-min", type=float)
    search.add_argument("--sentiment-max", type=float)
    search.add_argument("--category-confidence-min", type=float)
    search.add_argument("--category-confidence-max", type=float)
    search.add_argument("--fake-min", type=float)
    search.add_argument("--fake-max", type=float)
    search.add_argument("--fake-label", action="append")
    search.add_argument("--fulltext", choices=["yes", "no"])
    search.add_argument("--text-len-min", type=int)
    search.add_argument("--text-len-max", type=int)
    search.add_argument("--min-sources", type=int, help="Сколько источников должны повторить тот же текст")
    search.add_argument("--fuzzy", action="store_true")
    search.add_argument("--limit", type=int, default=20)
    archive = sub.add_parser("archive", help="Старые месяцы в Parquet (zstd), на PostgreSQL можно удалить раздел")
    archive.add_argument("--before", required=True, help="YYYY-MM-DD, не включая эту дату")
    archive.add_argument("--out", type=Path, default=Path("data/archive"))
    archive.add_argument("--drop", action="store_true")
    migrate = sub.add_parser("migrate-sqlite", help="Перенести SQLite-базу в текущий database.url")
    migrate.add_argument("--sqlite", required=True, help="sqlite:///путь")
    bench = sub.add_parser("bench", help="Размер и задержка на синтетических статьях")
    bench.add_argument("--rows", type=int, default=1_000_000)
    bench.add_argument("--postgres", default="postgresql+psycopg://fast:fast@127.0.0.1:5432/fast_parser_bench")
    bench.add_argument("--out", type=Path, default=Path("data/bench"))
    return parser


def _ml_installed() -> bool:
    from fast_parser.pipeline.ml_models import ml_available

    return ml_available()
