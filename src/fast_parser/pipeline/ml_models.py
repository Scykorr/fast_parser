"""Lazy CPU models. Import of torch/transformers happens only when a model is used."""

from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

_SENTIMENT_LABELS = {
    "positive": "positive",
    "negative": "negative",
    "neutral": "neutral",
    "позитив": "positive",
    "негатив": "negative",
    "нейтрально": "neutral",
    "label_0": "neutral",
    "label_1": "positive",
    "label_2": "negative",
}


def ml_available() -> bool:
    try:
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


class RubertSentimentModel:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._pipe = None

    def predict(self, texts: list[str]) -> list[tuple[str, float]]:
        pipe = self._pipeline()
        if not texts:
            return []
        raw = pipe(texts, batch_size=min(len(texts), 16), truncation=True, max_length=512)
        if raw and isinstance(raw[0], list):
            raw = [item[0] for item in raw]
        parsed: list[tuple[str, float]] = []
        for item in raw:
            label = _SENTIMENT_LABELS.get(str(item["label"]).casefold(), "neutral")
            parsed.append((label, float(item["score"])))
        return parsed

    def _pipeline(self):
        if self._pipe is None:
            from transformers import pipeline

            log.info("loading sentiment model %s", self.model_name)
            self._pipe = pipeline(
                "text-classification",
                model=self.model_name,
                tokenizer=self.model_name,
                device=-1,
                truncation=True,
            )
        return self._pipe


class RubertCategoryEncoder:
    """Cosine similarity of mean-pooled rubert-tiny2 embeddings to category descriptions."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._tokenizer = None
        self._model = None
        self._prototypes: dict[tuple[str, ...], object] = {}

    def embed(self, texts: list[str]):
        """Mean-pooled embeddings. The linear category head trains on these and does not update the encoder."""
        if not texts:
            return []
        tokenizer, model = self._load()
        return self._embed(texts, tokenizer, model).tolist()

    def similarities(self, texts: list[str], prototypes: list[tuple[str, str]]) -> list[dict[str, float]]:
        import torch

        if not texts:
            return []
        tokenizer, model = self._load()
        proto = self._prototype_matrix(prototypes, tokenizer, model)
        encoded = self._embed(texts, tokenizer, model)
        proto_n = torch.nn.functional.normalize(proto, dim=1)
        text_n = torch.nn.functional.normalize(encoded, dim=1)
        cosine = text_n @ proto_n.T
        titles = [title for title, _ in prototypes]
        rows: list[dict[str, float]] = []
        for line in cosine.tolist():
            rows.append({title: max(0.0, float(score)) for title, score in zip(titles, line)})
        return rows

    def _load(self):
        if self._model is None:
            from transformers import AutoModel, AutoTokenizer

            log.info("loading category encoder %s", self.model_name)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModel.from_pretrained(self.model_name)
            self._model.eval()
        return self._tokenizer, self._model

    def _prototype_matrix(self, prototypes, tokenizer, model):
        key = tuple(f"{title}\n{description}" for title, description in prototypes)
        cached = self._prototypes.get(key)
        if cached is None:
            descriptions = [f"{title}. {description}" for title, description in prototypes]
            cached = self._embed(descriptions, tokenizer, model)
            self._prototypes[key] = cached
        return cached

    def _embed(self, texts: list[str], tokenizer, model):
        import torch

        encoded = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="pt",
        )
        with torch.no_grad():
            hidden = model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).float()
            summed = (hidden * mask).sum(dim=1)
            counts = mask.sum(dim=1).clamp(min=1.0)
            return summed / counts


@lru_cache(maxsize=4)
def sentiment_model(name: str) -> RubertSentimentModel:
    return RubertSentimentModel(name)


@lru_cache(maxsize=4)
def category_encoder(name: str) -> RubertCategoryEncoder:
    return RubertCategoryEncoder(name)
