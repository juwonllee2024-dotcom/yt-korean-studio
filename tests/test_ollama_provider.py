import json

import httpx
import pytest

from backend.app.providers.translation.ollama import (
    OllamaTranslationProvider,
    TranslationProviderError,
    list_local_models,
)
from backend.app.subtitles.types import SubtitleSegment


def test_provider_preserves_ids_and_uses_json_only():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.5:4b"
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["keep_alive"] == "30m"
        assert payload["options"]["temperature"] == 0
        assert "Preserve every ID" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={"message": {"content": '[{"id": 1, "text": "안녕하세요."}]'}},
        )

    provider = OllamaTranslationProvider(
        "http://ollama.test",
        "qwen3.5:4b",
        transport=httpx.MockTransport(handler),
    )
    result = provider.translate_batch(
        [SubtitleSegment(1, 0, 1000, "Hello.")], "en", "ko"
    )
    assert result[0].id == 1
    assert result[0].start_ms == 0
    assert result[0].korean_text == "안녕하세요."


def test_provider_rejects_missing_or_duplicate_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": '[{"id": 2, "text": "잘못된 ID"}, {"id": 2, "text": "중복"}]'
                }
            },
        )

    provider = OllamaTranslationProvider(
        "http://ollama.test", "qwen3.5:4b", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(TranslationProviderError, match="IDs"):
        provider.translate_batch(
            [
                SubtitleSegment(1, 0, 1000, "One"),
                SubtitleSegment(2, 1000, 2000, "Two"),
            ],
            "en",
            "ko",
        )


def test_provider_rejects_markdown_wrapped_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": "```json\n[{\"id\": 1, \"text\": \"안녕\"}]\n```"}},
        )

    provider = OllamaTranslationProvider(
        "http://ollama.test", "qwen3.5:4b", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(TranslationProviderError, match="valid JSON"):
        provider.translate_batch([SubtitleSegment(1, 0, 1000, "Hi")], "en", "ko")


def test_provider_retries_transient_empty_json_response():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        content = "[]" if attempts < 3 else '[{"id": 1, "text": "안녕하세요."}]'
        return httpx.Response(200, json={"message": {"content": content}})

    provider = OllamaTranslationProvider(
        "http://ollama.test", "qwen3.5:4b", transport=httpx.MockTransport(handler)
    )
    result = provider.translate_batch(
        [SubtitleSegment(1, 0, 1000, "Hello.")], "en", "ko"
    )
    assert attempts == 3
    assert result[0].korean_text == "안녕하세요."


def test_list_local_models_reads_ollama_tags():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}, {"name": "custom:latest"}]})

    assert list_local_models(
        "http://ollama.test", transport=httpx.MockTransport(handler)
    ) == ["qwen3.5:4b", "custom:latest"]
