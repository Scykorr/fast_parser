import pytest

from fast_parser.pipeline.base import ProcessorResult
from fast_parser.pipeline.runner import Pipeline
from fast_parser.pipeline.stubs import CategoryStub, build_processors
from fast_parser.rss.parser import ParsedItem
from fast_parser.storage import upsert_article


class _Sentiment:
    name = "sentiment"

    def process(self, article):
        assert article.title == "Заголовок"
        assert article.source_id == "alpha"
        return ProcessorResult(
            handled=True,
            category="политика",
            sentiment="negative",
            sentiment_score=0.91,
            fake_score=0.2,
        )


def test_custom_processor_fills_nullable_columns(source_feed):
    session, feed = source_feed
    result = upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=ParsedItem(
            guid="1",
            url="https://alpha.example/news/1",
            title="Заголовок",
            summary="Анонс",
            text="Текст",
            authors=["Автор"],
            published_at=None,
            origin="rss",
        ),
        language="ru",
    )
    pipeline = Pipeline([CategoryStub(), _Sentiment()])
    assert pipeline.apply(result.article) is True
    assert result.article.category == "политика"
    assert result.article.sentiment == "negative"
    assert result.article.sentiment_score == 0.91
    assert result.article.fake_score == 0.2
    assert result.article.pipeline_status == "processed"


def test_stubs_do_not_mark_the_article_processed(source_feed):
    session, feed = source_feed
    result = upsert_article(
        session,
        source_id="alpha",
        feed_id=feed.id,
        item=ParsedItem(
            guid="1",
            url="https://alpha.example/news/1",
            title="Заголовок",
            summary=None,
            text="Текст",
            authors=[],
            published_at=None,
        ),
        language="ru",
    )
    pipeline = Pipeline(build_processors(["category", "sentiment", "fake_score"]))
    assert pipeline.apply(result.article) is False
    assert result.article.pipeline_status == "pending"
    assert result.article.sentiment is None


def test_sentiment_vocabulary_is_closed():
    with pytest.raises(ValueError):
        ProcessorResult(handled=True, sentiment="angry")


def test_unknown_processor_name_is_rejected():
    with pytest.raises(KeyError):
        build_processors(["category", "magic"])
