from pathlib import Path

import httpx
import pytest

from fast_parser.config import AppSettings, LlmSettings, load_settings
from fast_parser.evaluate import load_labels, score_dataset
from fast_parser.pipeline.base import ArticleView
from fast_parser.pipeline.classify import CategoryProcessor, SentimentProcessor
from fast_parser.pipeline.keywords import lexical_scores
from fast_parser.pipeline.llm import LlmFallback
from fast_parser.pipeline.taxonomy import load_taxonomy
from fast_parser.process import process_pending
from fast_parser.rss.parser import ParsedItem
from fast_parser.service import open_session
from fast_parser.storage import upsert_article

from conftest import ROOT, settings_for

TAXONOMY = load_taxonomy(ROOT / "config" / "categories.yaml")


def _view(title: str, text: str = "", rss: tuple[str, ...] = ()) -> ArticleView:
    return ArticleView(
        source_id="tass",
        url="https://example.com/n",
        title=title,
        text=text,
        summary=None,
        authors=(),
        published_at=None,
        language="ru",
        rss_categories=rss,
    )


def _settings() -> AppSettings:
    settings = AppSettings()
    settings.categories_path = ROOT / "config" / "categories.yaml"
    settings.pipeline.category.use_embeddings = False
    settings.pipeline.llm.enabled = False
    settings.pipeline.min_confidence = 0.45
    return settings


def test_rss_rubric_maps_to_sport():
    scores = lexical_scores(
        title="Коротко",
        text="",
        rss_categories=("Спорт",),
        taxonomy=TAXONOMY,
    )
    assert scores["Спорт"] >= 0.7
    result = CategoryProcessor(_settings(), TAXONOMY).process(_view("Коротко", rss=("Спорт",)))
    assert result.category == "Спорт"
    assert result.handled is True


def test_budget_headline_is_economy_and_bland_text_is_skipped():
    processor = CategoryProcessor(_settings(), TAXONOMY)
    economy = processor.process(_view("Центробанк повысил ключевую ставку из-за инфляции"))
    assert economy.category == "Экономика"
    bland = processor.process(_view("Заголовок", "Подробный текст материала без примет."))
    assert bland.handled is False
    assert bland.category is None


def test_sentiment_without_ml_extra_is_a_noop(monkeypatch):
    monkeypatch.setattr("fast_parser.pipeline.classify.ml_available", lambda: False)
    result = SentimentProcessor(_settings(), TAXONOMY).process(_view("Пожар уничтожил склад"))
    assert result.handled is False
    assert result.sentiment is None


def test_llm_fallback_fills_a_low_confidence_item(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"category": "Культура", "sentiment": "neutral", "confidence": 0.8}'
                        }
                    }
                ]
            },
            request=request,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = _settings()
    settings.pipeline.llm = LlmSettings(enabled=True, base_url="http://llm.test/v1", model="qwen")
    settings.pipeline.min_confidence = 0.9
    result = CategoryProcessor(settings, TAXONOMY).process(_view("Непонятная заметка"))
    assert result.category == "Культура"
    label = LlmFallback(settings.pipeline.llm, [item.title for item in TAXONOMY]).classify("а", "б")
    assert label[1] == "neutral"


def test_process_backfills_keywords_and_leaves_sentiment_pending(tmp_path):
    settings = settings_for(
        tmp_path,
        """
sources:
  - id: demo
    name: Демо
    type: test
    site: https://demo.example
    status: ok
    rss: []
""",
    )
    settings.categories_path = ROOT / "config" / "categories.yaml"
    settings.pipeline.category.use_embeddings = False
    session = open_session(settings)
    try:
        from fast_parser.models import Feed

        feed = Feed(source_id="demo", url="https://demo.example/rss", enabled=False)
        session.add(feed)
        session.commit()
        upsert_article(
            session,
            source_id="demo",
            feed_id=feed.id,
            item=ParsedItem(
                guid="1",
                url="https://demo.example/1",
                title="Матч завершился победой сборной",
                summary=None,
                text="Чемпионат мира по футболу, тренер доволен.",
                authors=[],
                published_at=None,
            ),
            language="ru",
        )
        session.commit()
    finally:
        session.close()

    stats = process_pending(settings)
    assert stats.seen == 1
    assert stats.with_category == 1
    assert stats.with_sentiment == 0
    assert stats.completed == 0
    session = open_session(settings)
    try:
        from fast_parser.models import Article

        article = session.query(Article).one()
        assert article.category == "Спорт"
        assert article.sentiment is None
        assert article.pipeline_status == "pending"
    finally:
        session.close()


def test_hand_labelled_set_is_scored_without_models():
    rows = load_labels(ROOT / "eval" / "labels.jsonl")
    assert len(rows) >= 150
    settings = load_settings(ROOT / "config" / "settings.yaml")
    settings.pipeline.category.use_embeddings = False
    settings.pipeline.llm.enabled = False
    keyword, sentiment, held_out_category, held_out_sentiment = score_dataset(
        settings, rows, use_embeddings=False
    )
    assert held_out_category is None
    assert held_out_sentiment is None
    assert sentiment is None
    assert keyword.labelled == len(rows)
    assert keyword.accuracy > 0.3


@pytest.mark.slow
def test_real_sentiment_model_labels_a_clear_sentence():
    pytest.importorskip("transformers")
    from fast_parser.pipeline.ml_models import RubertSentimentModel

    label, score = RubertSentimentModel("seara/rubert-tiny2-russian-sentiment").predict(
        ["Это ужасная трагедия, погибли люди."]
    )[0]
    assert label == "negative"
    assert score > 0.4
