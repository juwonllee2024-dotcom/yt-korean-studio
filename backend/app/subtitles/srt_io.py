from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from .types import SubtitleFormatError, SubtitleSegment

_TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})(?:\s+.*)?$"
)


def _parse_timestamp(value: str) -> int:
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise SubtitleFormatError(f"invalid timestamp: {value}")
    try:
        minute_value = int(minutes)
        second_value, millisecond_value = seconds.split(".")
        second_value = int(second_value)
        millisecond_value = int(millisecond_value.ljust(3, "0")[:3])
        hour_value = int(hours)
    except (ValueError, AttributeError) as exc:
        raise SubtitleFormatError(f"invalid timestamp: {value}") from exc
    if minute_value >= 60 or second_value >= 60:
        raise SubtitleFormatError(f"invalid timestamp: {value}")
    return ((hour_value * 60 + minute_value) * 60 + second_value) * 1000 + millisecond_value


def _parse_block(block: list[str], cue_id: int | None) -> SubtitleSegment:
    timing_index = next((i for i, line in enumerate(block) if "-->" in line), None)
    if timing_index is None:
        raise SubtitleFormatError("subtitle cue has no timing line")
    match = _TIMING_RE.match(block[timing_index].strip())
    if not match:
        raise SubtitleFormatError(f"invalid timing line: {block[timing_index]}")
    text = "\n".join(line.rstrip() for line in block[timing_index + 1 :]).strip()
    resolved_id = cue_id
    if resolved_id is None:
        prefix = block[:timing_index]
        if prefix and prefix[0].strip().isdigit():
            resolved_id = int(prefix[0].strip())
    if resolved_id is None:
        raise SubtitleFormatError("subtitle cue has no numeric id")
    return SubtitleSegment(
        id=resolved_id,
        start_ms=_parse_timestamp(match.group("start")),
        end_ms=_parse_timestamp(match.group("end")),
        text=text,
    )


def validate_segments(segments: Sequence[SubtitleSegment]) -> None:
    seen: set[int] = set()
    for segment in segments:
        if segment.id in seen:
            raise SubtitleFormatError(f"duplicate cue id {segment.id}")
        seen.add(segment.id)
        if segment.start_ms < 0:
            raise SubtitleFormatError(f"cue {segment.id} start time is negative")
        if segment.end_ms <= segment.start_ms:
            raise SubtitleFormatError(f"cue {segment.id} end time must be after start time")
        if not segment.text.strip():
            raise SubtitleFormatError(f"cue {segment.id} has empty text")


def parse_subtitles(path: Path) -> list[SubtitleSegment]:
    suffix = path.suffix.lower()
    if suffix not in {".srt", ".vtt"}:
        raise SubtitleFormatError(f"unsupported subtitle extension: {suffix or '(none)'}")
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SubtitleFormatError(f"cannot read subtitle file: {path}") from exc
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if suffix == ".vtt":
        if lines and lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]
        lines = [line for line in lines if not line.startswith("NOTE ")]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    segments: list[SubtitleSegment] = []
    for index, block in enumerate(blocks, start=1):
        if suffix == ".srt":
            if not block or not block[0].strip().isdigit():
                raise SubtitleFormatError(f"subtitle cue {index} has no numeric id")
            segment = _parse_block(block, int(block[0].strip()))
        else:
            segment = _parse_block(block, index)
        segments.append(segment)
    validate_segments(segments)
    return segments


def _format_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_srt(segments: Sequence[SubtitleSegment], path: Path) -> None:
    validate_segments(segments)
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for segment in segments:
        blocks.append(
            "\n".join(
                [
                    str(segment.id),
                    f"{_format_timestamp(segment.start_ms)} --> {_format_timestamp(segment.end_ms)}",
                    segment.text.strip(),
                ]
            )
        )
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")
