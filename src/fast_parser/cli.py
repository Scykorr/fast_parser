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

            result = process_pending(settings, limit=args.limit)
            print(
                "Разметка завершена\n"
                f"  просмотрено {result.seen}, с рубрикой {result.with_category}, "
                f"с тональностью {result.with_sentiment}, готовы {result.completed}"
            )
            if not _ml_installed():
                print("  модель тональности не установлена: pip install -e \".[ml]\"")
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
    return parser


def _ml_installed() -> bool:
    from fast_parser.pipeline.ml_models import ml_available

    return ml_available()
