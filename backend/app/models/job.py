from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    TRANSLATING = "translating"
    FORMATTING = "formatting"
    RENDERING = "rendering"
    CONCATENATING = "concatenating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_running(self) -> bool:
        return self in {
            JobStatus.QUEUED,
            JobStatus.PARSING,
            JobStatus.TRANSLATING,
            JobStatus.FORMATTING,
            JobStatus.RENDERING,
            JobStatus.CONCATENATING,
        }


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    status: JobStatus
    source_path: Path
    subtitle_path: Path | None
    intro_path: Path | None
    model: str
    current_stage: str
    progress: float
    error: str | None
    created_at: str
    updated_at: str
    stage_state: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "source_path": str(self.source_path),
            "subtitle_path": str(self.subtitle_path) if self.subtitle_path else None,
            "intro_path": str(self.intro_path) if self.intro_path else None,
            "model": self.model,
            "current_stage": self.current_stage,
            "progress": self.progress,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stage_state": self.stage_state,
        }
