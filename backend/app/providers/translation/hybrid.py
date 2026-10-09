from __future__ import annotations

import re
from typing import Sequence

from ...subtitles.types import SubtitleSegment, TranslatedSegment
from .ollama import TranslationProviderError


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")


def _needs_review(source: SubtitleSegment, translated: TranslatedSegment) -> bool:
    source_text = source.text.strip()
    translated_text = translated.korean_text.strip()
    if not translated_text or translated_text.casefold() == source_text.casefold():
        return True
    if "\ufffd" in translated_text or "<unk>" in translated_text.casefold():
        return True
    if len(source_text) > 160:
        return True
    tokens = _TOKEN_RE.findall(source_text)
    if any(token.isupper() and len(token) > 1 for token in tokens[1:]):
        return True
    return any(char.isdigit() for char in source_text)


class HybridTranslationProvider:
    """Translate everything with a fast provider, then refine suspicious cues."""

    def __init__(
        self,
        *,
        fast_provider,
        review_provider,
        max_review_cues: int = 24,
    ) -> None:
        if max_review_cues < 0:
            raise ValueError("max_review_cues cannot be negative")
        self.fast_provider = fast_provider
        self.review_provider = review_provider
        self.max_review_cues = max_review_cues
        self._fast_unavailable = False

    def translate_batch(
        self,
        segments: Sequence[SubtitleSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[TranslatedSegment]:
        if not segments:
            return []
        if self._fast_unavailable:
            return self.review_provider.translate_batch(segments, source_lang, target_lang)
        try:
            fast_results = self.fast_provider.translate_batch(segments, source_lang, target_lang)
        except TranslationProviderError:
            # Do not probe a missing optional model again for every subtitle batch.
            self._fast_unavailable = True
            return self.review_provider.translate_batch(segments, source_lang, target_lang)

        if len(fast_results) != len(segments):
            raise TranslationProviderError("fast translation returned a different number of cues")
        fast_by_id = {item.id: item for item in fast_results}
        if len(fast_by_id) != len(segments) or any(segment.id not in fast_by_id for segment in segments):
            raise TranslationProviderError("fast translation returned mismatched cue IDs")

        review_segments = [
            segment
            for segment in segments
            if _needs_review(segment, fast_by_id[segment.id])
        ][: self.max_review_cues]
        if not review_segments:
            return [fast_by_id[segment.id] for segment in segments]

        try:
            reviewed = self.review_provider.translate_batch(review_segments, source_lang, target_lang)
        except TranslationProviderError:
            # The fast translation is still usable if the optional quality pass is unavailable.
            return [fast_by_id[segment.id] for segment in segments]
        reviewed_by_id = {item.id: item for item in reviewed}
        if len(reviewed_by_id) != len(review_segments) or any(
            segment.id not in reviewed_by_id for segment in review_segments
        ):
            raise TranslationProviderError("review translation returned mismatched cue IDs")
        return [reviewed_by_id.get(segment.id, fast_by_id[segment.id]) for segment in segments]

    def close(self) -> None:
        seen: set[int] = set()
        for provider in (self.fast_provider, self.review_provider):
            marker = id(provider)
            if marker in seen:
                continue
            seen.add(marker)
            close = getattr(provider, "close", None)
            if callable(close):
                close()


__all__ = ["HybridTranslationProvider"]
