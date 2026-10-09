from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..models.job import JobStatus
from ..media.youtube import DownloadedMedia, YouTubeDownloadError, validate_youtube_url
from ..services.job_manager import JobStore
from ..services.storage import StorageError


router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_CHUNK_SIZE = 1024 * 1024
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
_SUBTITLE_EXTENSIONS = {".srt", ".vtt"}


async def _save_upload(
    upload: UploadFile,
    staging_dir: Path,
    allowed_extensions: set[str],
    label: str,
) -> Path:
    filename = upload.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"{label} must use one of: {', '.join(sorted(allowed_extensions))}",
        )
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / f"{uuid.uuid4().hex}{suffix}"
    total = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await upload.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="uploaded file is too large")
                handle.write(chunk)
    finally:
        await upload.close()
    return target


def _validate_model(model: str | None, default: str) -> str:
    value = (model or default).strip()
    if not _MODEL_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="model name contains unsupported characters")
    return value


def _run_job(pipeline, store: JobStore, job_id: str) -> None:
    try:
        pipeline.run(job_id)
    except Exception as exc:
        # Pipeline normally records its own failures. This guard also covers injected/test pipelines.
        try:
            store.update(
                job_id,
                status=JobStatus.FAILED,
                current_stage="failed",
                error=str(exc).strip()[:500] or exc.__class__.__name__,
            )
        except Exception:
            pass


@router.post("")
async def create_job(
    request: Request,
    video: UploadFile | None = File(None),
    subtitle: UploadFile | None = File(None),
    intro: UploadFile | None = File(None),
    model: str | None = Form(None),
    youtube_url: str | None = Form(None),
    rights_confirmed: bool = Form(False),
) -> dict[str, str]:
    settings = request.app.state.settings
    store: JobStore = request.app.state.store
    pipeline = request.app.state.pipeline
    selected_model = _validate_model(model, settings.default_model)
    has_upload = video is not None
    has_url = bool((youtube_url or "").strip())
    if has_upload == has_url:
        raise HTTPException(status_code=400, detail="choose either a video file or a YouTube URL")
    if has_url and not rights_confirmed:
        raise HTTPException(status_code=400, detail="confirm that you have the right to use this video")
    staging_dir = settings.data_dir / ".incoming" / uuid.uuid4().hex
    try:
        if has_url:
            try:
                validated_url = validate_youtube_url(youtube_url or "")
                staging_dir.mkdir(parents=True, exist_ok=True)
                downloaded: DownloadedMedia = await asyncio.to_thread(
                    request.app.state.youtube_downloader.download,
                    validated_url,
                    staging_dir,
                )
                for path in (downloaded.video_path, downloaded.subtitle_path):
                    resolved = Path(path).resolve()
                    try:
                        resolved.relative_to(staging_dir.resolve())
                    except ValueError as exc:
                        raise YouTubeDownloadError("downloader returned an unsafe path") from exc
                    if not resolved.is_file():
                        raise YouTubeDownloadError("downloader returned a missing file")
                video_path = downloaded.video_path
                subtitle_path = downloaded.subtitle_path
            except YouTubeDownloadError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            video_path = await _save_upload(video, staging_dir, _VIDEO_EXTENSIONS, "video")
            subtitle_path = (
                await _save_upload(subtitle, staging_dir, _SUBTITLE_EXTENSIONS, "subtitle")
                if subtitle is not None
                else None
            )
        intro_path = (
            await _save_upload(intro, staging_dir, _VIDEO_EXTENSIONS, "intro video")
            if intro is not None
            else None
        )
        try:
            job = store.create(
                video_path,
                subtitle_path,
                intro_path,
                model=selected_model,
            )
        except (StorageError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    request.app.state.executor.submit(_run_job, pipeline, store, job.id)
    return {"job_id": job.id}


@router.get("/{job_id}")
def get_job(request: Request, job_id: str) -> dict:
    store: JobStore = request.app.state.store
    try:
        job = store.get(job_id)
    except (KeyError, StorageError) as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    files = request.app.state.file_catalog(job.id)
    return {
        "job_id": job.id,
        "status": job.status.value,
        "stage": job.current_stage,
        "progress": job.progress,
        "error": job.error,
        "model": job.model,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "files": files,
    }


@router.post("/{job_id}/cancel")
def cancel_job(request: Request, job_id: str) -> dict:
    store: JobStore = request.app.state.store
    pipeline = request.app.state.pipeline
    try:
        pipeline.cancel(job_id)
        job = store.get(job_id)
    except (KeyError, StorageError) as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    return {
        "job_id": job.id,
        "status": job.status.value,
        "stage": job.current_stage,
        "progress": job.progress,
        "error": job.error,
    }
