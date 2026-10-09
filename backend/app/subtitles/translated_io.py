from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pysubs2

from .srt_io import write_srt
from .types import TranslatedSegment, SubtitleSegment


@dataclass(frozen=True, slots=True)
class SubtitleStyle:
    font_name: str = "Arial"
    font_size: float = 80.0
    outline: float = 3.0
    shadow: float = 1.0
    margin_vertical: int = 48


def write_translated_srt(segments: Sequence[TranslatedSegment], path: Path) -> None:
    write_srt(
        [
            SubtitleSegment(
                id=segment.id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.korean_text,
            )
            for segment in segments
        ],
        path,
    )


def write_ass(
    segments: Sequence[TranslatedSegment],
    path: Path,
    style: SubtitleStyle | None = None,
) -> None:
    selected_style = style or SubtitleStyle()
    subtitles = pysubs2.SSAFile()
    # Declare the design canvas so libass does not scale 44pt text from its
    # tiny legacy default resolution into oversized captions on HD video.
    subtitles.info["PlayResX"] = "1920"
    subtitles.info["PlayResY"] = "1080"
    subtitles.styles["Default"] = pysubs2.SSAStyle(
        fontname=selected_style.font_name,
        fontsize=selected_style.font_size,
        primarycolor=pysubs2.Color(255, 255, 255, 0),
        outlinecolor=pysubs2.Color(0, 0, 0, 0),
        outline=selected_style.outline,
        shadow=selected_style.shadow,
        marginv=selected_style.margin_vertical,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
    )
    for segment in segments:
        subtitles.events.append(
            pysubs2.SSAEvent(
                start=segment.start_ms,
                end=segment.end_ms,
                text=segment.korean_text.replace("\n", r"\N"),
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    subtitles.save(path, encoding="utf-8")
