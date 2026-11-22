"""One sentence for the case where stored articles have no sentiment yet."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

SENTIMENT_HINT = (
    'Тональность не посчитана: установите pip install -e ".[ml]" '
    "и выполните fast-parser process"
)


def sentiment_uncomputed(session: Session) -> bool:
    """True when no stored article has a sentiment label, including an empty database."""
    from sqlalchemy import func, select

    from fast_parser.models import Article

    labelled = session.scalar(
        select(func.count()).select_from(Article).where(Article.sentiment.is_not(None))
    )
    return not labelled


def warn_if_sentiment_uncomputed(session: Session) -> None:
    if sentiment_uncomputed(session):
        logging.getLogger(__name__).warning(SENTIMENT_HINT)
