from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from fast_parser import __version__
from fast_parser.config import AppSettings, load_settings
from fast_parser.db import init_db
from fast_parser.service import open_session, run_once
from fast_parser.search_filters import facet_counts, parse_filters
from fast_parser.web.queries import (
    ArticleFilters,
    article_with_source,
    dashboard,
    region_coverage,
    search_articles,
    snippets,
    source_statuses,
)
from fast_parser.web.schemas import (
    ArticleDetail,
    ArticleListItem,
    ArticlePage,
    CollectResult,
    FeedStatus,
    RegionCoverage,
    RegionReport,
    SourceStatus,
    StatsReport,
)

_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(_ROOT / "templates"))


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    synced = open_session(settings)
    synced.close()
    factory = init_db(settings.database.url)

    app = FastAPI(
        title="fast_parser",
        version=__version__,
        description="Локальный сборщик российских государственных СМИ: лента, источники и сводка.",
    )
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")

    def db_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def _filters(
        source_id: list[str] = Query(default_factory=list, description="Источник, можно несколько"),
        source_id_not: list[str] = Query(default_factory=list, description="Исключить источники"),
        source_type: list[str] = Query(default_factory=list, description="Тип источника"),
        source_type_not: list[str] = Query(default_factory=list),
        source_status: list[str] = Query(default_factory=list, description="Состояние источника в каталоге"),
        source_status_not: list[str] = Query(default_factory=list),
        official: str | None = Query(default=None, description="yes — подтверждённый официальный источник"),
        region: list[str] = Query(default_factory=list, description="Название региона"),
        region_not: list[str] = Query(default_factory=list),
        region_code: list[str] = Query(default_factory=list, description="Код субъекта"),
        region_code_not: list[str] = Query(default_factory=list),
        federal_district: list[str] = Query(default_factory=list),
        federal_district_not: list[str] = Query(default_factory=list),
        category: list[str] = Query(default_factory=list, description="Рубрика, можно несколько"),
        category_not: list[str] = Query(default_factory=list, description="Исключить рубрики"),
        sentiment: list[str] = Query(default_factory=list),
        sentiment_not: list[str] = Query(default_factory=list),
        channel: list[str] = Query(default_factory=list, description="rss, telegram, vk или x"),
        channel_not: list[str] = Query(default_factory=list),
        language: list[str] = Query(default_factory=list),
        language_not: list[str] = Query(default_factory=list),
        pipeline_status: list[str] = Query(default_factory=list, description="pending или processed"),
        pipeline_status_not: list[str] = Query(default_factory=list),
        tag: list[str] = Query(default_factory=list, description="Метка из RSS"),
        tag_not: list[str] = Query(default_factory=list),
        author: list[str] = Query(default_factory=list),
        author_not: list[str] = Query(default_factory=list),
        domain: list[str] = Query(default_factory=list),
        domain_not: list[str] = Query(default_factory=list),
        fake_label: list[str] = Query(default_factory=list, description="Метка недостоверности, появится позже"),
        fake_label_not: list[str] = Query(default_factory=list),
        weekday: list[int] = Query(default_factory=list, description="День недели публикации, 1=понедельник … 7=воскресенье, Москва"),
        hour: list[int] = Query(default_factory=list, description="Час публикации, 0–23, Москва"),
        url: str | None = None,
        date_on: str = Query(default="published", description="published или collected"),
        period: str | None = Query(default=None, description="today, 24h, 7d, 30d, month, 365d"),
        date_from: str | None = None,
        date_to: str | None = None,
        q: str | None = Query(default=None, description="Слова в заголовке и тексте: фраза, OR, -исключить"),
        title_q: str | None = Query(default=None, description="Слова только в заголовке"),
        body_q: str | None = Query(default=None, description="Слова только в тексте"),
        fuzzy: bool = False,
        sentiment_min: float | None = None,
        sentiment_max: float | None = None,
        category_confidence_min: float | None = None,
        category_confidence_max: float | None = None,
        fake_score_min: float | None = None,
        fake_score_max: float | None = None,
        has_fulltext: str | None = None,
        text_len_min: int | None = None,
        text_len_max: int | None = None,
        min_cluster_sources: int | None = Query(default=None, description="Текст встретился минимум у стольких источников"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> ArticleFilters:
        return parse_filters(locals(), limit=limit, offset=offset)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/facets")
    def api_facets(filters: ArticleFilters = Depends(_filters), session: Session = Depends(db_session)):
        """Счётчики значений при текущем фильтре. Свой фильтр фасета в подсчёт не входит."""
        return {"total": search_articles(session, filters)[1], "facets": facet_counts(session, filters)}

    @app.get("/api/articles", response_model=ArticlePage)
    def api_articles(filters: ArticleFilters = Depends(_filters), session: Session = Depends(db_session)):
        """Та же модель фильтра, что у страницы и у команды search. Параметр можно повторить, *_not исключает значение."""
        rows, total = search_articles(session, filters)
        return ArticlePage(
            total=total,
            limit=filters.limit,
            offset=filters.offset,
            items=[_list_item(article, source) for article, source in rows],
        )

    @app.get("/api/articles/{article_id}", response_model=ArticleDetail)
    def api_article(article_id: int, session: Session = Depends(db_session)):
        found = article_with_source(session, article_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Статья не найдена")
        return _detail(*found)

    @app.get("/api/sources", response_model=list[SourceStatus])
    def api_sources(session: Session = Depends(db_session)):
        return [_source_status(row) for row in source_statuses(session)]

    @app.get("/api/regions", response_model=RegionReport)
    def api_regions(session: Session = Depends(db_session)):
        items = [RegionCoverage.model_validate(row) for row in region_coverage(session)]
        return RegionReport(
            subjects_total=len(items),
            subjects_covered=sum(1 for item in items if item.covered),
            items=items,
        )

    @app.get("/api/stats", response_model=StatsReport)
    def api_stats(session: Session = Depends(db_session)):
        return StatsReport.model_validate(dashboard(session))

    @app.post("/api/collect", response_model=CollectResult)
    def api_collect():
        return _collect_result()

    @app.get("/", response_class=HTMLResponse)
    def feed_page(
        request: Request,
        filters: ArticleFilters = Depends(_filters),
        session: Session = Depends(db_session),
    ):
        rows, total = search_articles(session, filters)
        context = {
            "request": request,
            "filters": filters,
            "facets": facet_counts(session, filters),
            "articles": rows,
            "snippets": snippets(session, rows, filters.text or filters.title_text or filters.body_text),
            "saved": _saved(session),
            "total": total,
            "version": __version__,
        }
        template = "feed_list.html" if request.headers.get("HX-Request") else "feed.html"
        return TEMPLATES.TemplateResponse(request, template, context)

    @app.get("/articles/{article_id}", response_class=HTMLResponse)
    def article_page(article_id: int, request: Request, session: Session = Depends(db_session)):
        found = article_with_source(session, article_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Статья не найдена")
        return TEMPLATES.TemplateResponse(
            request,
            "article.html",
            {"request": request, "article": found[0], "source": found[1], "version": __version__},
        )

    @app.get("/sources", response_class=HTMLResponse)
    def sources_page(request: Request, session: Session = Depends(db_session)):
        return TEMPLATES.TemplateResponse(
            request,
            "sources.html",
            {"request": request, "rows": source_statuses(session), "version": __version__},
        )

    @app.get("/regions", response_class=HTMLResponse)
    def regions_page(request: Request, session: Session = Depends(db_session)):
        items = region_coverage(session)
        return TEMPLATES.TemplateResponse(
            request,
            "regions.html",
            {
                "request": request,
                "items": items,
                "covered": sum(1 for item in items if item["covered"]),
                "total": len(items),
                "version": __version__,
            },
        )

    @app.get("/stats", response_class=HTMLResponse)
    def stats_page(request: Request, session: Session = Depends(db_session)):
        return TEMPLATES.TemplateResponse(
            request,
            "stats.html",
            {"request": request, "stats": dashboard(session), "version": __version__},
        )

    @app.post("/searches")
    async def save_search(request: Request, session: Session = Depends(db_session)):
        from fast_parser.models import SavedSearch
        from fast_parser.timeutil import utcnow

        form = await request.form()
        name = str(form.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Нужно имя поиска")
        filters = parse_filters(form)
        payload = filters.to_query()
        existing = session.scalar(select(SavedSearch).where(SavedSearch.name == name))
        if existing is None:
            session.add(SavedSearch(name=name, query_json=payload, created_at=utcnow()))
        else:
            existing.query_json = payload
        session.commit()
        return RedirectResponse("/", status_code=303)

    @app.get("/export.csv")
    def export_csv(filters: ArticleFilters = Depends(_filters), session: Session = Depends(db_session)):
        from fastapi.responses import Response

        filters.limit = 5000
        filters.offset = 0
        rows, _total = search_articles(session, filters)
        body = _csv(rows)
        return Response(body, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=articles.csv"})

    @app.get("/export.parquet")
    def export_parquet(filters: ArticleFilters = Depends(_filters), session: Session = Depends(db_session)):
        from fastapi.responses import Response

        filters.limit = 5000
        filters.offset = 0
        rows, _total = search_articles(session, filters)
        payload = _parquet(rows)
        return Response(
            payload,
            media_type="application/vnd.apache.parquet",
            headers={"Content-Disposition": "attachment; filename=articles.parquet"},
        )

    @app.post("/collect")
    def collect_page(request: Request):
        result = _collect_result()
        if request.headers.get("HX-Request"):
            return TEMPLATES.TemplateResponse(
                request,
                "collect_status.html",
                {"request": request, "result": result},
            )
        query = f"/?collected=1&new={result.items_new}&errors={result.feeds_error}"
        return RedirectResponse(query, status_code=303)

    def _collect_result() -> CollectResult:
        stats = run_once(settings)
        return CollectResult(
            feeds_total=stats.feeds_total,
            feeds_ok=stats.feeds_ok,
            feeds_not_modified=stats.feeds_not_modified,
            feeds_empty=stats.feeds_empty,
            feeds_error=stats.feeds_error,
            items_fetched=stats.items_fetched,
            items_new=stats.items_new,
            items_duplicate=stats.items_duplicate,
            items_updated=stats.items_updated,
            errors=[f"{url}: {error}" for url, error in stats.errors],
        )

    return app


def _saved(session: Session) -> list:
    from fast_parser.models import SavedSearch

    return list(session.scalars(select(SavedSearch).order_by(SavedSearch.name)))


def _csv(rows) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "source", "title", "published_at", "category", "sentiment", "url"])
    for article, source in rows:
        writer.writerow(
            [
                article.id,
                source.id,
                article.title,
                article.published_at.isoformat() if article.published_at else "",
                article.category or "",
                article.sentiment or "",
                article.canonical_url,
            ]
        )
    return buffer.getvalue()


def _parquet(rows) -> bytes:
    import duckdb

    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE export_batch (id BIGINT, source_id VARCHAR, title VARCHAR, url VARCHAR, category VARCHAR, sentiment VARCHAR)"
    )
    connection.executemany(
        "INSERT INTO export_batch VALUES (?,?,?,?,?,?)",
        [(article.id, source.id, article.title, article.canonical_url, article.category, article.sentiment) for article, source in rows],
    )
    path = "/tmp/fast-parser-export.parquet"
    connection.execute(f"COPY export_batch TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    connection.close()
    return Path(path).read_bytes()


def _list_item(article, source) -> ArticleListItem:
    return ArticleListItem(
        id=article.id,
        source_id=source.id,
        source_name=source.name,
        source_type=source.type,
        region=source.region,
        region_code=source.region_code,
        federal_district=source.federal_district,
        title=article.title,
        summary=article.summary,
        url=article.canonical_url,
        published_at=article.published_at,
        category=article.category,
        sentiment=article.sentiment,
        fake_score=article.fake_score,
    )


def _detail(article, source) -> ArticleDetail:
    base = _list_item(article, source)
    return ArticleDetail(
        **base.model_dump(),
        text=article.text or "",
        authors=list(article.authors or []),
        sentiment_score=article.sentiment_score,
        content_origin=article.content_origin,
    )


def _source_status(row: dict) -> SourceStatus:
    source = row["source"]
    return SourceStatus(
        id=source.id,
        name=source.name,
        type=source.type,
        site=source.site,
        status=source.status,
        region=source.region,
        region_code=source.region_code,
        federal_district=source.federal_district,
        telegram_username=source.telegram_username,
        feeds=[FeedStatus.model_validate(feed) for feed in row["feeds"]],
    )
