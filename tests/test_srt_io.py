from pathlib import Path

import pytest

from backend.app.subtitles.srt_io import (
    SubtitleFormatError,
    parse_subtitles,
    validate_segments,
    write_srt,
)


def test_parse_and_round_trip_preserves_ids_and_timestamps(tmp_path):
    source = Path("tests/fixtures/sample.srt")
    segments = parse_subtitles(source)
    assert [(s.id, s.start_ms, s.end_ms) for s in segments] == [
        (1, 1250, 3200),
        (2, 3300, 5100),
    ]
    assert segments[0].text == "Hello\nworld."

    target = tmp_path / "roundtrip.srt"
    write_srt(segments, target)
    assert [(s.id, s.start_ms, s.end_ms) for s in parse_subtitles(target)] == [
        (1, 1250, 3200),
        (2, 3300, 5100),
    ]


def test_parse_vtt_strips_header_and_cue_settings(tmp_path):
    source = tmp_path / "sample.vtt"
    source.write_text(
        "WEBVTT\n\n"
        "00:00:00.500 --> 00:00:01.750 position:20%\n"
        "Welcome\n",
        encoding="utf-8",
    )
    segments = parse_subtitles(source)
    assert [(s.id, s.start_ms, s.end_ms, s.text) for s in segments] == [
        (1, 500, 1750, "Welcome")
    ]


def test_validation_rejects_duplicate_ids_and_invalid_ranges():
    from backend.app.subtitles.types import SubtitleSegment

    with pytest.raises(SubtitleFormatError, match="duplicate cue id 1"):
        validate_segments(
            [
                SubtitleSegment(1, 0, 1000, "one"),
                SubtitleSegment(1, 1100, 1200, "two"),
            ]
        )

    with pytest.raises(SubtitleFormatError, match="cue 2 end time"):
        validate_segments([SubtitleSegment(2, 1000, 500, "backwards")])


def test_parser_rejects_empty_text(tmp_path):
    source = tmp_path / "empty.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\n\n", encoding="utf-8")
    with pytest.raises(SubtitleFormatError, match="cue 1 has empty text"):
        parse_subtitles(source)
