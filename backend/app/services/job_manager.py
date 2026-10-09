from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.job import Job, JobStatus
from .storage import JobStorage, StorageError


_UNSET = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Create, update, and retrieve resumable local subtitle jobs."""

    def __init__(self, data_dir: Path) -> None:
        self.storage = JobStorage(Path(data_dir))

    @property
    def data_dir(self) -> Path:
        return self.storage.data_dir

    def job_dir(self, job_id: str) -> Path:
        return self.storage.job_dir(job_id)

    def create(
        self,
        video: Path,
        subtitle: Path | None,
        intro: Path | None,
        *,
        model: str,
        job_id: str | None = None,
    ) -> Job:
        resolved_id = job_id or uuid.uuid4().hex
        self.storage.validate_job_id(resolved_id)
        created_at = _now()
        try:
            # Check before copying any bytes into an existing job directory.
            with self.storage.connection() as connection:
                if connection.execute("SELECT 1 FROM jobs WHERE id = ?", (resolved_id,)).fetchone():
                    raise ValueError(f"job already exists: {resolved_id}")
            source_path, subtitle_path, intro_path = self.storage.copy_job_inputs(
                resolved_id, Path(video), Path(subtitle) if subtitle else None, Path(intro) if intro else None
            )
            with self.storage.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, status, source_path, subtitle_path, intro_path, model,
                        current_stage, progress, error, created_at, updated_at, stage_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_id,
                        JobStatus.QUEUED.value,
                        str(source_path),
                        str(subtitle_path) if subtitle_path else None,
                        str(intro_path) if intro_path else None,
                        model,
                        "queued",
                        0.0,
                        None,
                        created_at,
                        created_at,
                        "{}",
                    ),
                )
        except Exception:
            # Do not remove an existing user's file. Only remove a newly prepared job directory.
            job_dir = self.storage.job_dir(resolved_id)
            if not self._job_exists(resolved_id) and job_dir.exists():
                for child in job_dir.rglob("*"):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                for child in sorted(job_dir.rglob("*"), reverse=True):
                    if child.is_dir():
                        child.rmdir()
                job_dir.rmdir()
            raise
        return self.get(resolved_id)

    def _job_exists(self, job_id: str) -> bool:
        with self.storage.connection() as connection:
            return connection.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is not None

    @staticmethod
    def _row_to_job(row: Any) -> Job:
        try:
            stage_state = json.loads(row["stage_state"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise StorageError(f"job {row['id']} has invalid stage state") from exc
        if not isinstance(stage_state, dict):
            raise StorageError(f"job {row['id']} has invalid stage state")
        return Job(
            id=row["id"],
            status=JobStatus(row["status"]),
            source_path=Path(row["source_path"]),
            subtitle_path=Path(row["subtitle_path"]) if row["subtitle_path"] else None,
            intro_path=Path(row["intro_path"]) if row["intro_path"] else None,
            model=row["model"],
            current_stage=row["current_stage"],
            progress=float(row["progress"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            stage_state=stage_state,
        )

    def get(self, job_id: str) -> Job:
        self.storage.validate_job_id(job_id)
        with self.storage.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown job: {job_id}")
        return self._row_to_job(row)

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus | str | None = None,
        current_stage: str | None = None,
        progress: float | None = None,
        error: str | None | object = _UNSET,
        stage_state: dict[str, dict[str, Any]] | None = None,
    ) -> Job:
        self.storage.validate_job_id(job_id)
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = JobStatus(status).value
        if current_stage is not None:
            values["current_stage"] = current_stage
        if progress is not None:
            if not 0 <= float(progress) <= 100:
                raise ValueError("progress must be between 0 and 100")
            values["progress"] = float(progress)
        if error is not _UNSET:
            values["error"] = error
        if stage_state is not None:
            values["stage_state"] = json.dumps(stage_state, ensure_ascii=False, sort_keys=True)
        values["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.storage.connection() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown job: {job_id}")
        return self.get(job_id)

    def set_stage_state(
        self,
        job_id: str,
        stage: str,
        *,
        fingerprint: str,
        outputs: list[Path],
    ) -> Job:
        job = self.get(job_id)
        state = dict(job.stage_state)
        job_dir = self.job_dir(job_id).resolve()
        relative_outputs: list[str] = []
        for output in outputs:
            resolved = Path(output).resolve()
            try:
                relative_outputs.append(str(resolved.relative_to(job_dir)))
            except ValueError as exc:
                raise StorageError("stage output must remain inside job directory") from exc
        state[stage] = {"fingerprint": fingerprint, "outputs": relative_outputs}
        return self.update(job_id, stage_state=state)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status.is_running:
            return self.update(
                job_id,
                status=JobStatus.CANCELLED,
                current_stage="cancelled",
                error="Cancelled by user",
            )
        return job
