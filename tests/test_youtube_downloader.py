from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.media.youtube import (
    YouTubeDownloadError,
    YouTubeDownloader,
    validate_youtube_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123?t=10",
        "https://www.youtube.com/shorts/abc123",
        "https://m.youtube.com/live/abc123",
    ],
)
def test_validate_youtube_url_accepts_video_hosts(url):
    assert validate_youtube_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=abc123",
        "javascript:alert(1)",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/playlist?list=abc123",
        "http://www.youtube.com/watch?v=abc123",
    ],
)
def test_validate_youtube_url_rejects_non_video_or_unsafe_urls(url):
    with pytest.raises(YouTubeDownloadError):
        validate_youtube_url(url)


def test_downloader_uses_fixed_safe_options_and_finds_english_subtitle(tmp_path):
    captured: dict[str, object] = {}

    class FakeYDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def extract_info(self, url, download=True):
            assert url == "https://youtu.be/abc123"
            assert download is True
            (tmp_path / "video.mp4").write_bytes(b"video")
            (tmp_path / "video.en.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
                encoding="utf-8",
            )
            return {"id": "abc123"}

    result = YouTubeDownloader(ytdlp_factory=FakeYDL).download(
        "https://youtu.be/abc123", tmp_path
    )

    assert result.video_path == tmp_path / "video.mp4"
    assert result.subtitle_path == tmp_path / "video.en.srt"
    assert captured["noplaylist"] is True
    assert captured["ignoreconfig"] is True
    assert captured["writesubtitles"] is True
    assert captured["writeautomaticsub"] is True
    assert captured["subtitleslangs"] == ["en", "en-US", "en-GB"]
    assert captured["sleep_interval_requests"] == 1
    assert captured["sleep_interval_subtitles"] == 3
    assert str(captured["outtmpl"]).endswith("video.%(ext)s")


def test_downloader_reports_missing_subtitles_without_leaking_url(tmp_path):
    class NoSubtitleYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def extract_info(self, _url, download=True):
            (tmp_path / "video.mp4").write_bytes(b"video")
            raise RuntimeError("blocked https://www.youtube.com/watch?v=secret")

    with pytest.raises(YouTubeDownloadError, match="English subtitles") as error:
        YouTubeDownloader(ytdlp_factory=NoSubtitleYDL).download(
            "https://www.youtube.com/watch?v=secret", tmp_path
        )
    assert "secret" not in str(error.value)


def test_downloader_turns_youtube_429_into_retryable_guidance(tmp_path):
    class RateLimitedYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def extract_info(self, _url, download=True):
            raise RuntimeError(
                "ERROR: Unable to download video subtitles for 'en-en-US': "
                "HTTP Error 429: Too Many Requests"
            )

    with pytest.raises(YouTubeDownloadError, match="rate-limited") as error:
        YouTubeDownloader(ytdlp_factory=RateLimitedYDL).download(
            "https://www.youtube.com/watch?v=abc123", tmp_path
        )
    message = str(error.value)
    assert "429" in message
    assert "Wait a few minutes" in message
    assert "en-en-US" not in message
