from __future__ import annotations

from backend.app.providers.translation.hybrid import HybridTranslationProvider
from backend.app.providers.translation.ollama import TranslationProviderError
from backend.app.subtitles.types import SubtitleSegment, TranslatedSegment


def _translated(segment: SubtitleSegment, text: str) -> TranslatedSegment:
    return TranslatedSegment(
        id=segment.id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        original_text=segment.text,
        korean_text=text,
    )


class FakeFastProvider:
    def __init__(self) -> None:
        self.calls: list[list[int]] = []

    def translate_batch(self, segments, source_lang, target_lang):
        self.calls.append([segment.id for segment in segments])
        return [_translated(segment, segment.text if segment.id == 2 else "빠른 번역") for segment in segments]


class FakeReviewProvider:
    def __init__(self) -> None:
        self.calls: list[list[int]] = []

    def translate_batch(self, segments, source_lang, target_lang):
        self.calls.append([segment.id for segment in segments])
        return [_translated(segment, "검토된 번역") for segment in segments]


def test_hybrid_reviews_only_suspicious_fast_results():
    fast = FakeFastProvider()
    review = FakeReviewProvider()
    provider = HybridTranslationProvider(fast_provider=fast, review_provider=review)
    segments = [
        SubtitleSegment(1, 0, 1000, "Hello."),
        SubtitleSegment(2, 1000, 2000, "OpenAI 2026"),
    ]

    result = provider.translate_batch(segments, "en", "ko")

    assert fast.calls == [[1, 2]]
    assert review.calls == [[2]]
    assert [item.korean_text for item in result] == ["빠른 번역", "검토된 번역"]


class FailingFastProvider:
    def __init__(self) -> None:
        self.calls = 0

    def translate_batch(self, segments, source_lang, target_lang):
        self.calls += 1
        raise TranslationProviderError("fast model unavailable")


def test_hybrid_falls_back_to_review_provider_when_fast_model_unavailable():
    fast = FailingFastProvider()
    review = FakeReviewProvider()
    provider = HybridTranslationProvider(
        fast_provider=fast, review_provider=review
    )
    segments = [SubtitleSegment(1, 0, 1000, "Hello.")]

    result = provider.translate_batch(segments, "en", "ko")

    assert review.calls == [[1]]
    assert result[0].korean_text == "검토된 번역"

    provider.translate_batch(segments, "en", "ko")
    assert fast.calls == 1
