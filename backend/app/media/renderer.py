from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from ..subtitles.translated_io import SubtitleStyle


class MediaError(RuntimeError):
    """Raised when a media input or FFmpeg operation is unsafe or fails."""


CommandRunner = Callable[[Sequence[str]], None]


def _run_command(args: Sequence[str]) -> None:
    try:
        completed = subprocess.run(
            list(args),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise MediaError("FFmpeg was not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "FFmpeg failed").strip().splitlines()[-1]
        raise MediaError(f"FFmpeg failed: {detail}") from exc
    if completed.returncode != 0:
        raise MediaError("FFmpeg returned a non-zero exit code")


def _validate_media(path: Path, suffixes: set[str], label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise MediaError(f"{label} does not exist: {path}")
    if resolved.suffix.lower() not in suffixes:
        raise MediaError(f"{label} has an unsupported extension: {resolved.suffix}")
    return resolved


def _validate_output(path: Path, suffix: str, source: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() != suffix:
        raise MediaError(f"output must use {suffix}")
    if resolved == source:
        raise MediaError("refusing to overwrite source media")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _subtitle_filter(ass_file: Path) -> str:
    # FFmpeg's subtitles filter treats Windows drive colons specially.
    value = ass_file.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
    return f"subtitles='{value}'"


def render_subtitles(
    video: Path,
    ass_file: Path,
    output: Path,
    *,
    runner: CommandRunner = _run_command,
) -> None:
    source = _validate_media(video, {".mp4", ".mov", ".mkv", ".webm"}, "video")
    subtitles = _validate_media(ass_file, {".ass"}, "ASS subtitle")
    target = _validate_output(output, ".mp4", source)
    runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            _subtitle_filter(subtitles),
            "-c:a",
            "copy",
            str(target),
        ]
    )


def _concat_line(path: Path) -> str:
    # concat demuxer accepts single-quoted paths with apostrophes escaped.
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'\n"


def concat_intro(
    intro: Path,
    video: Path,
    output: Path,
    *,
    runner: CommandRunner = _run_command,
) -> None:
    intro_path = _validate_media(intro, {".mp4", ".mov", ".mkv", ".webm"}, "intro video")
    video_path = _validate_media(video, {".mp4", ".mov", ".mkv", ".webm"}, "video")
    target = _validate_output(output, ".mp4", video_path)
    if target == intro_path:
        raise MediaError("refusing to overwrite intro media")
    concat_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="ytks-concat-",
            dir=target.parent,
            delete=False,
        ) as handle:
            concat_path = Path(handle.name)
            handle.write(_concat_line(intro_path))
            handle.write(_concat_line(video_path))
        runner(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                str(target),
            ]
        )
    finally:
        if concat_path is not None:
            concat_path.unlink(missing_ok=True)
