from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

from .types import TranslatedSegment


MIN_DURATION_MS = 1_000
MAX_DURATION_MS = 7_000
MAX_LINES = 2
MAX_LINE_CHARS = 21
SHORT_GAP_MAX_MS = 1_000


def normalize_segments_for_display(
    segments: Sequence[TranslatedSegment],
) -> list[TranslatedSegment]:
    """Prevent adjacent subtitle events from rendering on top of each other.

    Auto-generated subtitle files often contain overlapping windows. Keep the
    next cue's start as the boundary and truncate the previous cue for a
    single readable display line at a time. Same-start cues are shifted by
    one millisecond so generated SRT/ASS remains valid.
    """

    normalized = sorted(segments, key=lambda segment: (segment.start_ms, segment.id))
    for index in range(1, len(normalized)):
        previous = normalized[index - 1]
        current = normalized[index]
        if current.start_ms >= previous.end_ms:
            continue
        boundary = current.start_ms
        if boundary <= previous.start_ms:
            boundary = previous.start_ms + 1
            current = replace(
                current,
                start_ms=boundary,
                end_ms=max(current.end_ms, boundary + 1),
            )
            normalized[index] = current
        normalized[index - 1] = replace(previous, end_ms=boundary)
    return normalized


@dataclass(frozen=True, slots=True)
class SubtitleIssue:
    cue_id: int
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class SubtitleQAReport:
    total_cues: int
    issues: tuple[SubtitleIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "standard": "pyconkr-guide",
            "passed": self.passed,
            "total_cues": self.total_cues,
            "issues": [issue.as_dict() for issue in self.issues],
            "rules": {
                "min_duration_ms": MIN_DURATION_MS,
                "max_duration_ms": MAX_DURATION_MS,
                "max_lines": MAX_LINES,
                "max_line_chars": MAX_LINE_CHARS,
                "short_gap_max_ms": SHORT_GAP_MAX_MS,
            },
        }


def _text_for(segment: Any) -> str:
    korean_text = getattr(segment, "korean_text", None)
    if korean_text is not None:
        return str(korean_text)
    return str(getattr(segment, "text", ""))


def check_pyconkr_guidelines(segments: Sequence[Any]) -> SubtitleQAReport:
    """Check structural rules from PyCon.KR's subtitle guide.

    Linguistic-boundary quality and speech onset timing need human/audio review;
    this report only checks facts available in subtitle cues.
    """

    issues: list[SubtitleIssue] = []
    for index, segment in enumerate(segments):
        cue_id = int(getattr(segment, "id"))
        start_ms = int(getattr(segment, "start_ms"))
        end_ms = int(getattr(segment, "end_ms"))
        duration_ms = end_ms - start_ms
        text = _text_for(segment).strip()
        lines = text.splitlines() or [text]

        if not text:
            issues.append(SubtitleIssue(cue_id, "empty_text", "cue has no subtitle text"))
        if duration_ms < MIN_DURATION_MS:
            issues.append(
                SubtitleIssue(
                    cue_id,
                    "duration_too_short",
                    f"cue duration is {duration_ms}ms; minimum is {MIN_DURATION_MS}ms",
                )
            )
        elif duration_ms > MAX_DURATION_MS:
            issues.append(
                SubtitleIssue(
                    cue_id,
                    "duration_too_long",
                    f"cue duration is {duration_ms}ms; maximum is {MAX_DURATION_MS}ms",
                )
            )
        if len(lines) > MAX_LINES:
            issues.append(
                SubtitleIssue(
                    cue_id,
                    "line_count",
                    f"cue has {len(lines)} lines; maximum is {MAX_LINES}",
                )
            )
        if any(len(line) > MAX_LINE_CHARS for line in lines):
            longest = max(len(line) for line in lines)
            issues.append(
                SubtitleIssue(
                    cue_id,
                    "line_length",
                    f"longest line has {longest} characters; maximum is {MAX_LINE_CHARS}",
                )
            )

        if index:
            previous = segments[index - 1]
            previous_end_ms = int(getattr(previous, "end_ms"))
            gap_ms = start_ms - previous_end_ms
            if 0 < gap_ms <= SHORT_GAP_MAX_MS:
                previous_id = int(getattr(previous, "id"))
                issues.append(
                    SubtitleIssue(
                        previous_id,
                        "short_gap",
                        f"gap before cue {cue_id} is {gap_ms}ms; merge adjacent cues when appropriate",
                    )
                )

    return SubtitleQAReport(total_cues=len(segments), issues=tuple(issues))
