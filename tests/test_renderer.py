from pathlib import Path

import pytest

from backend.app.media.renderer import (
    MediaError,
    SubtitleStyle,
    concat_intro,
    render_subtitles,
)
from backend.app.subtitles.translated_io import write_ass, write_translated_srt
from backend.app.subtitles.types import TranslatedSegment


def _segments():
    return [
        TranslatedSegment(1, 0, 1200, "Hello", "안녕하세요."),
        TranslatedSegment(2, 1300, 2600, "World", "세계입니다."),
    ]


def test_translated_writers_keep_timing_and_korean_text(tmp_path):
    srt_path = tmp_path / "korean.srt"
    ass_path = tmp_path / "korean.ass"
    write_translated_srt(_segments(), srt_path)
    write_ass(_segments(), ass_path, SubtitleStyle(font_size=44))

    assert "안녕하세요." in srt_path.read_text(encoding="utf-8")
    ass_text = ass_path.read_text(encoding="utf-8")
    assert "[Events]" in ass_text
    assert "안녕하세요." in ass_text
    assert "0:00:00.00,0:00:01.20" in ass_text


def test_ass_declares_hd_play_resolution_for_predictable_font_size(tmp_path):
    ass_path = tmp_path / "korean.ass"

    write_ass(_segments(), ass_path, SubtitleStyle(font_size=44))

    ass_text = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1920" in ass_text
    assert "PlayResY: 1080" in ass_text


def test_default_subtitle_style_is_readable_on_hd_video():
    assert SubtitleStyle().font_size == 80.0


def test_render_uses_fixed_ffmpeg_flags_and_validated_paths(tmp_path):
    commands = []
    video = tmp_path / "source.mp4"
    ass = tmp_path / "korean.ass"
    output = tmp_path / "output.mp4"
    video.write_bytes(b"video")
    ass.write_text("[Script Info]", encoding="utf-8")

    render_subtitles(video, ass, output, runner=lambda args: commands.append(args))
    assert commands[0][0] == "ffmpeg"
    assert "-vf" in commands[0]
    assert "-y" in commands[0]
    assert str(output) in commands[0]


def test_render_rejects_overwriting_source(tmp_path):
    video = tmp_path / "source.mp4"
    ass = tmp_path / "korean.ass"
    video.write_bytes(b"video")
    ass.write_text("[Script Info]", encoding="utf-8")
    with pytest.raises(MediaError, match="overwrite"):
        render_subtitles(video, ass, video, runner=lambda _args: None)


def test_concat_intro_writes_a_safe_concat_command(tmp_path):
    commands = []
    intro = tmp_path / "intro.mp4"
    video = tmp_path / "video.mp4"
    output = tmp_path / "final.mp4"
    intro.write_bytes(b"intro")
    video.write_bytes(b"video")
    concat_intro(intro, video, output, runner=lambda args: commands.append(args))
    assert commands[0][:5] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    assert "-f" in commands[0]
    assert "concat" in commands[0]
