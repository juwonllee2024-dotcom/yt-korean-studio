from __future__ import annotations

from typing import Protocol, Sequence

from ...subtitles.types import SubtitleSegment, TranslatedSegment


class TranslationProvider(Protocol):
    def translate_batch(
        self,
        segments: Sequence[SubtitleSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[TranslatedSegment]: ...
