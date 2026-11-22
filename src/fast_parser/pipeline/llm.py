"""Optional OpenAI-compatible fallback, for example a local Ollama model."""

from __future__ import annotations

import json
import logging
import re

import httpx

from fast_parser.config import LlmSettings
from fast_parser.pipeline.base import ALLOWED_SENTIMENTS

log = logging.getLogger(__name__)


class LlmFallback:
    def __init__(self, settings: LlmSettings, categories: list[str]) -> None:
        self.settings = settings
        self.categories = categories

    def classify(self, title: str, text: str) -> tuple[str | None, str | None, float]:
        if not self.settings.enabled:
            return None, None, 0.0
        prompt = (
            "Ты размечаешь новость. Верни только JSON "
            '{"category": "...", "sentiment": "positive|neutral|negative", "confidence": 0.0}.\n'
            f"Рубрика строго одна из: {', '.join(self.categories)}.\n"
            "sentiment: positive — хорошая новость для героя или страны, "
            "negative — ущерб, конфликт, критика, neutral — сухая справка.\n"
            f"Заголовок: {title}\n"
            f"Текст: {text[:2000]}"
        )
        try:
            raw = self._complete(prompt)
        except Exception as exc:
            log.warning("llm fallback failed: %s", exc)
            return None, None, 0.0
        return _parse(raw, self.categories)

    def _complete(self, prompt: str) -> str:
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        response = httpx.post(
            url,
            headers=headers,
            json={
                "model": self.settings.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"]


def _parse(raw: str, categories: list[str]) -> tuple[str | None, str | None, float]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None, None, 0.0
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, None, 0.0
    category = data.get("category")
    if category not in categories:
        category = None
    sentiment = str(data.get("sentiment") or "").casefold()
    if sentiment not in ALLOWED_SENTIMENTS:
        sentiment = None
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return category, sentiment, confidence
