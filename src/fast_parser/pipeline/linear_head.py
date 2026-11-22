"""A linear softmax head. Training is numpy; the encoder underneath stays frozen."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import math


@dataclass
class LinearHead:
    labels: list[str]
    mean: list[float]
    std: list[float]
    weight: list[list[float]]
    bias: list[float]

    def predict(self, features: list[list[float]]) -> list[tuple[str, float]]:
        found: list[tuple[str, float]] = []
        for row in features:
            scaled = [(value - center) / scale for value, center, scale in zip(row, self.mean, self.std)]
            logits = []
            for column, bias in zip(self.weight, self.bias):
                logits.append(sum(value * coef for value, coef in zip(scaled, column)) + bias)
            shift = max(logits)
            exps = [math.exp(item - shift) for item in logits]
            total = sum(exps) or 1.0
            probs = [item / total for item in exps]
            index = max(range(len(probs)), key=probs.__getitem__)
            found.append((self.labels[index], probs[index]))
        return found

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "labels": self.labels,
                    "mean": self.mean,
                    "std": self.std,
                    "weight": self.weight,
                    "bias": self.bias,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> LinearHead:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            labels=list(payload["labels"]),
            mean=list(payload["mean"]),
            std=list(payload["std"]),
            weight=payload["weight"],
            bias=list(payload["bias"]),
        )


def fit_softmax(
    features: list[list[float]],
    labels: list[int],
    *,
    n_classes: int,
    epochs: int = 240,
    lr: float = 0.35,
    l2: float = 0.4,
    seed: int = 0,
) -> tuple[list[float], list[float], list[list[float]], list[float]]:
    """Class-weighted softmax regression. Regularisation is fixed, not tuned on a test fold."""
    import numpy as np

    matrix_x = np.asarray(features, dtype=np.float64)
    mean = matrix_x.mean(axis=0)
    std = matrix_x.std(axis=0)
    std[std < 1e-8] = 1.0
    scaled = (matrix_x - mean) / std
    targets = np.asarray(labels, dtype=np.int64)
    counts = np.bincount(targets, minlength=n_classes).astype(np.float64)
    class_weight = np.zeros(n_classes)
    present = counts > 0
    class_weight[present] = len(targets) / (n_classes * counts[present])
    rng = np.random.default_rng(seed)
    weight = rng.normal(0.0, 0.01, size=(n_classes, scaled.shape[1]))
    bias = np.zeros(n_classes)
    example_weight = class_weight[targets]
    for _ in range(epochs):
        logits = scaled @ weight.T + bias
        logits -= logits.max(axis=1, keepdims=True)
        probs = np.exp(logits)
        probs /= probs.sum(axis=1, keepdims=True)
        residual = probs
        residual[np.arange(len(targets)), targets] -= 1.0
        residual *= example_weight[:, None] / len(targets)
        weight -= lr * (residual.T @ scaled + l2 * weight)
        bias -= lr * residual.sum(axis=0)
    return mean.tolist(), std.tolist(), weight.tolist(), bias.tolist()


def stratified_folds(labels: list[str], folds: int, seed: int = 0) -> list[list[int]]:
    import random

    grouped: dict[str, list[int]] = {}
    for index, label in enumerate(labels):
        grouped.setdefault(label, []).append(index)
    rng = random.Random(seed)
    buckets: list[list[int]] = [[] for _ in range(folds)]
    for indexes in grouped.values():
        rng.shuffle(indexes)
        for offset, index in enumerate(indexes):
            buckets[offset % folds].append(index)
    return buckets
