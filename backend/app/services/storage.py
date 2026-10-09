from __future__ import annotations

import re
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class StorageError(RuntimeError):
    """Raised when job storage cannot safely prepare a file or database."""


_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
_SUBTITLE_EXTENSIONS = {".srt", ".vtt"}


class JobStorage:
    """Filesystem and SQLite boundary for local jobs."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.database_path = self.data_dir / "jobs.sqlite3"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    subtitle_path TEXT,
                    intro_path TEXT,
                    model TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    progress REAL NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    stage_state TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def validate_job_id(self, job_id: str) -> str:
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise StorageError("job ID must contain only letters, numbers, hyphens, or underscores")
        return job_id

    def job_dir(self, job_id: str) -> Path:
        safe_id = self.validate_job_id(job_id)
        return self.jobs_dir / safe_id

    def prepare_job_dirs(self, job_id: str) -> Path:
        job_dir = self.job_dir(job_id)
        for name in ("source", "subtitles", "output", "logs"):
            (job_dir / name).mkdir(parents=True, exist_ok=True)
        return job_dir

    @staticmethod
    def _validate_file(path: Path, extensions: set[str], label: str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise StorageError(f"{label} does not exist: {path}")
        if resolved.suffix.lower() not in extensions:
            raise StorageError(f"{label} has unsupported extension: {resolved.suffix or '(none)'}")
        return resolved

    @staticmethod
    def _copy_if_needed(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)

    def copy_job_inputs(
        self,
        job_id: str,
        video: Path,
        subtitle: Path | None,
        intro: Path | None,
    ) -> tuple[Path, Path | None, Path | None]:
        job_dir = self.prepare_job_dirs(job_id)
        video_source = self._validate_file(video, _VIDEO_EXTENSIONS, "video")
        video_target = job_dir / "source" / f"video{video_source.suffix.lower()}"
        self._copy_if_needed(video_source, video_target)

        subtitle_target: Path | None = None
        if subtitle is not None:
            subtitle_source = self._validate_file(subtitle, _SUBTITLE_EXTENSIONS, "subtitle")
            subtitle_target = job_dir / "subtitles" / f"original{subtitle_source.suffix.lower()}"
            self._copy_if_needed(subtitle_source, subtitle_target)

        intro_target: Path | None = None
        if intro is not None:
            intro_source = self._validate_file(intro, _VIDEO_EXTENSIONS, "intro video")
            intro_target = job_dir / "source" / f"intro{intro_source.suffix.lower()}"
            self._copy_if_needed(intro_source, intro_target)

        (job_dir / "logs" / "job.log").touch(exist_ok=True)
        return video_target, subtitle_target, intro_target
