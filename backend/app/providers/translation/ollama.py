from __future__ import annotations

import json
import time
from typing import Sequence

import httpx

from ...subtitles.types import SubtitleSegment, TranslatedSegment
from .base import TranslationProvider


class TranslationProviderError(RuntimeError):
    """Raised when a local translation provider cannot return valid output."""


_TRANSLATION_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "text": {"type": "string"},
        },
        "required": ["id", "text"],
        "additionalProperties": False,
    },
}


def _prompt(segments: Sequence[SubtitleSegment], source_lang: str, target_lang: str) -> str:
    payload = [{"id": s.id, "text": s.text} for s in segments]
    return (
        f"Translate these subtitle segments from {source_lang} to {target_lang}.\n"
        f"Return exactly {len(segments)} objects, one object for each input segment. "
        "Preserve every ID. Do not merge IDs. Do not omit information. "
        "Translate using surrounding context. Produce natural spoken Korean. "
        "Keep subtitles concise. Return valid JSON only as an array of objects "
        "with exactly the keys id and text.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


class OllamaTranslationProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 3,
        retry_delay_s: float = 0.25,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_delay_s < 0:
            raise ValueError("retry_delay_s cannot be negative")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_attempts = max_attempts
        self.retry_delay_s = retry_delay_s
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaTranslationProvider":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _translate_batch_once(
        self,
        segments: Sequence[SubtitleSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[TranslatedSegment]:
        if not segments:
            return []
        try:
            response = self._client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "think": False,
                    # Keep Qwen warm while a long subtitle job sends review batches.
                    "keep_alive": "30m",
                    "options": {"temperature": 0},
                    "format": _TRANSLATION_SCHEMA,
                    "messages": [{"role": "user", "content": _prompt(segments, source_lang, target_lang)}],
                },
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TranslationProviderError(f"Ollama request failed: {exc}") from exc

        try:
            content = body["message"]["content"]
            decoded = json.loads(content)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise TranslationProviderError("Ollama response is not valid JSON") from exc
        if not isinstance(decoded, list):
            raise TranslationProviderError("Ollama response must be a JSON array")

        expected_ids = [segment.id for segment in segments]
        received_ids = [item.get("id") for item in decoded if isinstance(item, dict)]
        if len(decoded) != len(segments) or received_ids != expected_ids:
            raise TranslationProviderError(
                f"Ollama response IDs do not match input IDs: expected {expected_ids}, received {received_ids}"
            )

        by_id: dict[int, str] = {}
        for item in decoded:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                raise TranslationProviderError("Ollama response IDs must be integers")
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise TranslationProviderError(f"Ollama response has empty text for ID {item.get('id')}")
            if item["id"] in by_id:
                raise TranslationProviderError(f"Ollama response contains duplicate ID {item['id']}")
            by_id[item["id"]] = text.strip()

        return [
            TranslatedSegment(
                id=segment.id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                original_text=segment.text,
                korean_text=by_id[segment.id],
            )
            for segment in segments
        ]

    def translate_batch(
        self,
        segments: Sequence[SubtitleSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[TranslatedSegment]:
        if not segments:
            return []
        last_error: TranslationProviderError | None = None
        for attempt in range(self.max_attempts):
            try:
                return self._translate_batch_once(segments, source_lang, target_lang)
            except TranslationProviderError as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    raise
                if self.retry_delay_s:
                    time.sleep(self.retry_delay_s * (attempt + 1))
        raise last_error or TranslationProviderError("Ollama translation failed")


def list_local_models(
    base_url: str,
    *,
    timeout_s: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise TranslationProviderError(f"Could not list Ollama models: {exc}") from exc
    names = [model.get("name") for model in models if isinstance(model, dict)]
    if any(not isinstance(name, str) or not name for name in names):
        raise TranslationProviderError("Ollama model list contains an invalid name")
    return names
