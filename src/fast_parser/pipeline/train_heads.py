"""Fit the linear heads and print stratified cross-validation.

The quoted accuracy is the pooled held-out folds. Weights saved for production
are fit afterwards on every labelled row; that in-sample score is not the claim.
"""

from __future__ import annotations

from pathlib import Path

from fast_parser.config import AppSettings, load_settings
from fast_parser.evaluate import load_labels, _metrics
from fast_parser.pipeline.head_features import category_feature, head_text, sentiment_feature
from fast_parser.pipeline.keywords import lexical_scores
from fast_parser.pipeline.linear_head import LinearHead, fit_softmax, stratified_folds
from fast_parser.pipeline.ml_models import category_encoder
from fast_parser.pipeline.taxonomy import load_taxonomy

HEAD_PATH = Path(__file__).resolve().parent / "data" / "category_head.json"
SENTIMENT_HEAD_PATH = Path(__file__).resolve().parent / "data" / "sentiment_head.json"
FOLDS = 3
SEED = 0


def category_features(rows: list[dict], settings: AppSettings) -> tuple[list[list[float]], list[str]]:
    taxonomy = load_taxonomy(settings.categories_path)
    titles = [item.title for item in taxonomy]
    encoder = category_encoder(settings.pipeline.category.model)
    texts = [head_text(row.get("title") or "", row.get("text") or "") for row in rows]
    vectors = encoder.embed(texts)
    prototypes = encoder.similarities(texts, [(item.title, item.description) for item in taxonomy])
    features: list[list[float]] = []
    for row, vector, proto in zip(rows, vectors, prototypes):
        lexical = lexical_scores(
            title=row.get("title") or "",
            text=row.get("text") or "",
            rss_categories=row.get("rss") or [],
            taxonomy=taxonomy,
        )
        features.append(category_feature(vector, proto, lexical, titles))
    return features, titles


def sentiment_features(rows: list[dict], settings: AppSettings) -> list[list[float]]:
    encoder = category_encoder(settings.pipeline.category.model)
    texts = [head_text(row.get("title") or "", row.get("text") or "") for row in rows]
    return [sentiment_feature(vector) for vector in encoder.embed(texts)]


def cross_validate(features: list[list[float]], gold: list[str], labels: list[str]) -> tuple[float, float, list[str]]:
    index = {label: pos for pos, label in enumerate(labels)}
    encoded = [index[label] for label in gold]
    predicted = [""] * len(gold)
    for fold, held in enumerate(stratified_folds(gold, FOLDS, SEED)):
        train = [pos for pos in range(len(gold)) if pos not in set(held)]
        mean, std, weight, bias = fit_softmax(
            [features[pos] for pos in train],
            [encoded[pos] for pos in train],
            n_classes=len(labels),
            seed=SEED + fold,
        )
        head = LinearHead(labels, mean, std, weight, bias)
        for row, (name, _score) in zip(held, head.predict([features[pos] for pos in held])):
            predicted[row] = name
    metrics = _metrics("cv", gold, predicted, labels)
    return metrics.accuracy, metrics.macro_f1, predicted


def fit_all(features: list[list[float]], gold: list[str], labels: list[str]) -> LinearHead:
    index = {label: pos for pos, label in enumerate(labels)}
    mean, std, weight, bias = fit_softmax(
        features,
        [index[label] for label in gold],
        n_classes=len(labels),
        seed=SEED,
    )
    return LinearHead(labels, mean, std, weight, bias)


def main() -> None:
    settings = load_settings()
    rows = load_labels(Path("eval/labels.jsonl"))
    features, titles = category_features(rows, settings)
    gold = [row["category"] for row in rows]
    accuracy, macro_f1, _predicted = cross_validate(features, gold, titles)
    print(f"category head CV accuracy {accuracy * 100:.1f}% macro-F1 {macro_f1 * 100:.1f}%")
    fit_all(features, gold, titles).dump(HEAD_PATH)
    print(f"saved {HEAD_PATH}")
    sentiment_gold = [row["sentiment"] for row in rows]
    sentiment_labels = ["positive", "neutral", "negative"]
    sent_features = sentiment_features(rows, settings)
    sent_acc, sent_f1, _sent_pred = cross_validate(sent_features, sentiment_gold, sentiment_labels)
    print(f"sentiment head CV accuracy {sent_acc * 100:.1f}% macro-F1 {sent_f1 * 100:.1f}%")
    fit_all(sent_features, sentiment_gold, sentiment_labels).dump(SENTIMENT_HEAD_PATH)
    print(f"saved {SENTIMENT_HEAD_PATH}")


if __name__ == "__main__":
    main()
