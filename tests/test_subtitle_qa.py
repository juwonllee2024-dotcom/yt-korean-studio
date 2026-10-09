from backend.app.subtitles.qa import check_pyconkr_guidelines, normalize_segments_for_display
from backend.app.subtitles.types import SubtitleSegment, TranslatedSegment


def test_normalize_display_segments_removes_overlaps_at_next_cue_start():
    segments = [
        TranslatedSegment(1, 0, 3360, "one", "하나"),
        TranslatedSegment(2, 1160, 5160, "two", "둘"),
        TranslatedSegment(3, 3360, 7040, "three", "셋"),
    ]

    normalized = normalize_segments_for_display(segments)

    assert [(segment.id, segment.start_ms, segment.end_ms) for segment in normalized] == [
        (1, 0, 1160),
        (2, 1160, 3360),
        (3, 3360, 7040),
    ]


def test_pyconkr_guidelines_report_structural_issues():
    segments = [
        SubtitleSegment(1, 0, 800, "짧은 자막"),
        SubtitleSegment(2, 1800, 10000, "가" * 22 + "\n둘째 줄\n셋째 줄"),
    ]

    report = check_pyconkr_guidelines(segments)

    assert report.total_cues == 2
    assert report.passed is False
    assert {(issue.cue_id, issue.code) for issue in report.issues} == {
        (1, "duration_too_short"),
        (1, "short_gap"),
        (2, "duration_too_long"),
        (2, "line_count"),
        (2, "line_length"),
    }


def test_pyconkr_guidelines_accept_a_clean_two_line_cue():
    report = check_pyconkr_guidelines(
        [SubtitleSegment(1, 0, 1000, "첫 번째 줄\n두 번째 줄")]
    )

    assert report.passed is True
    assert report.issues == ()
