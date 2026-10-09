from __future__ import annotations

from dataclasses import dataclass


class SubtitleFormatError(ValueError):
    """Raised when a subtitle file cannot be safely processed."""


@dataclass(frozen=True, slots=True)
class SubtitleSegment:
    id: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class TranslatedSegment:
    id: int
    start_ms: int
    end_ms: int
    original_text: str
    korean_text: str
