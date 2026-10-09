from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from typing import Any, Callable


class YouTubeDownloadError(RuntimeError):
    """Raised when a safe YouTube download or subtitle lookup fails."""


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    video_path: Path
    subtitle_path: Path


_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
_SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_RATE_LIMIT_RE = re.compile(r"(?:\b429\b|too many requests|rate[ -]?limit)", re.IGNORECASE)
_ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}


def validate_youtube_url(url: str) -> str:
    value = (url or "").strip()
    if len(value) > 2048:
        raise YouTubeDownloadError("YouTube URL is too long")
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise YouTubeDownloadError("YouTube URL is malformed") from exc
    if parsed.scheme != "https" or hostname not in _ALLOWED_HOSTS or port is not None:
        raise YouTubeDownloadError("only an HTTPS YouTube video URL is allowed")
    if parsed.username or parsed.password:
        raise YouTubeDownloadError("YouTube URL cannot contain credentials")

    path_parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if hostname in {"youtu.be", "www.youtu.be"}:
        if path_parts:
            video_id = path_parts[0]
    elif parsed.path.rstrip("/") == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    elif len(path_parts) >= 2 and path_parts[0].lower() in {"shorts", "live", "embed"}:
        video_id = path_parts[1]
    if not video_id or not _VIDEO_ID_RE.fullmatch(video_id):
        raise YouTubeDownloadError("URL must identify one YouTube video, not a playlist")
    return value


def _safe_error(error: Exception) -> str:
    message = _URL_RE.sub("<url>", str(error)).strip()
    if _RATE_LIMIT_RE.search(message):
        return (
            "YouTube request was rate-limited (HTTP 429). "
            "Wait a few minutes and retry, or use local video + English .srt mode."
        )
    return message[:300] or error.__class__.__name__


def _default_factory(options: dict[str, Any]):
    try:
        import yt_dlp
    except ImportError as exc:
        raise YouTubeDownloadError("yt-dlp is not installed; run setup_windows.ps1") from exc
    return yt_dlp.YoutubeDL(options)


YtdlpFactory = Callable[[dict[str, Any]], Any]


class YouTubeDownloader:
    """Download one rights-confirmed video and one English subtitle sidecar."""

    def __init__(self, *, ytdlp_factory: YtdlpFactory | None = None) -> None:
        self.ytdlp_factory = ytdlp_factory or _default_factory

    @staticmethod
    def _options(output_dir: Path) -> dict[str, Any]:
        return {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": str(output_dir / "video.%(ext)s"),
            "noplaylist": True,
            "ignoreconfig": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            "socket_timeout": 30,
            "sleep_interval_requests": 1,
            "sleep_interval_subtitles": 3,
            "writesubtitles": True,
            "writeautomaticsub": True,
            # Avoid en.*: YouTube can expose translated variants such as en-en-US.
            "subtitleslangs": ["en", "en-US", "en-GB"],
            "subtitlesformat": "srt/vtt",
            "convertsubtitles": "srt",
            "overwrites": False,
        }

    @staticmethod
    def _inside(directory: Path, candidate: Path) -> bool:
        try:
            candidate.resolve().relative_to(directory.resolve())
            return True
        except ValueError:
            return False

    @classmethod
    def _find_video(cls, output_dir: Path) -> Path | None:
        candidates = [
            path
            for path in output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in _VIDEO_EXTENSIONS and cls._inside(output_dir, path)
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda path: (path.name.lower() != "video.mp4", path.name.lower()))[0]

    @classmethod
    def _find_subtitle(cls, output_dir: Path) -> Path | None:
        candidates = [
            path
            for path in output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in _SUBTITLE_EXTENSIONS and cls._inside(output_dir, path)
        ]
        if not candidates:
            return None
        english = [path for path in candidates if re.search(r"(?:^|[._-])en(?:[._-]|$)", path.stem, re.IGNORECASE)]
        pool = english or candidates
        return sorted(pool, key=lambda path: (path.suffix.lower() != ".srt", path.name.lower()))[0]

    def download(self, url: str, output_dir: Path) -> DownloadedMedia:
        validated_url = validate_youtube_url(url)
        directory = Path(output_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with self.ytdlp_factory(self._options(directory)) as downloader:
                downloader.extract_info(validated_url, download=True)
        except YouTubeDownloadError:
            raise
        except Exception as exc:
            video = self._find_video(directory)
            if video is None:
                raise YouTubeDownloadError(f"YouTube download failed: {_safe_error(exc)}") from exc

        video_path = self._find_video(directory)
        if video_path is None:
            raise YouTubeDownloadError("YouTube download produced no video file")
        subtitle_path = self._find_subtitle(directory)
        if subtitle_path is None:
            raise YouTubeDownloadError("English subtitles were not available for this video")
        return DownloadedMedia(video_path=video_path, subtitle_path=subtitle_path)


__all__ = ["DownloadedMedia", "YouTubeDownloadError", "YouTubeDownloader", "validate_youtube_url"]
