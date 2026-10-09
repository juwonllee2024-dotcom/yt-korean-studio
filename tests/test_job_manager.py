from pathlib import Path

import pytest

from backend.app.models.job import JobStatus
from backend.app.services.job_manager import JobStore


def _source_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    video = tmp_path / "camera.mp4"
    subtitle = tmp_path / "english.srt"
    intro = tmp_path / "intro.mp4"
    video.write_bytes(b"video")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    intro.write_bytes(b"intro")
    return video, subtitle, intro


def test_create_materializes_job_directory_and_preserves_inputs(tmp_path):
    video, subtitle, intro = _source_files(tmp_path)
    store = JobStore(tmp_path / "data")

    job = store.create(video, subtitle, intro, model="qwen3.5:4b", job_id="job-1")

    job_dir = tmp_path / "data" / "jobs" / "job-1"
    assert job.status is JobStatus.QUEUED
    assert Path(job.source_path) == job_dir / "source" / "video.mp4"
    assert Path(job.subtitle_path) == job_dir / "subtitles" / "original.srt"
    assert Path(job.intro_path) == job_dir / "source" / "intro.mp4"
    assert (job_dir / "output").is_dir()
    assert (job_dir / "logs").is_dir()
    assert Path(job.source_path).read_bytes() == b"video"
    assert video.read_bytes() == b"video"


def test_update_and_cancel_are_persisted(tmp_path):
    video, subtitle, _ = _source_files(tmp_path)
    store = JobStore(tmp_path / "data")
    store.create(video, subtitle, None, model="qwen3.5:4b", job_id="job-2")

    store.update("job-2", current_stage="translating", progress=42.5)
    updated = store.get("job-2")
    assert updated.current_stage == "translating"
    assert updated.progress == 42.5
    assert updated.status is JobStatus.QUEUED

    store.cancel("job-2")
    cancelled = store.get("job-2")
    assert cancelled.status is JobStatus.CANCELLED


def test_missing_job_raises_key_error(tmp_path):
    store = JobStore(tmp_path / "data")
    with pytest.raises(KeyError):
        store.get("missing")


def test_duplicate_job_id_does_not_overwrite_existing_source(tmp_path):
    video, subtitle, _ = _source_files(tmp_path)
    store = JobStore(tmp_path / "data")
    store.create(video, subtitle, None, model="qwen3.5:4b", job_id="same")
    video.write_bytes(b"new video")

    with pytest.raises(ValueError, match="already exists"):
        store.create(video, subtitle, None, model="qwen3.5:4b", job_id="same")

    assert (tmp_path / "data" / "jobs" / "same" / "source" / "video.mp4").read_bytes() == b"video"
