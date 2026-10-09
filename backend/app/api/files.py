from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..services.storage import StorageError


router = APIRouter(prefix="/api/jobs", tags=["files"])

_FILE_LOCATIONS = {
    "original.srt": ("subtitles", "original.srt"),
    "original.vtt": ("subtitles", "original.vtt"),
    "korean.srt": ("subtitles", "korean.srt"),
    "korean.ass": ("subtitles", "korean.ass"),
    "qa.json": ("subtitles", "qa.json"),
    "subtitled.mp4": ("output", "subtitled.mp4"),
    "final.mp4": ("output", "final.mp4"),
    "metadata.json": ("", "metadata.json"),
    "transcript.json": ("", "transcript.json"),
}


def catalog_files(job_dir: Path) -> list[str]:
    return [
        name
        for name, (folder, filename) in _FILE_LOCATIONS.items()
        if (job_dir / folder / filename).is_file()
    ]


def resolve_generated_file(job_dir: Path, file_name: str) -> Path:
    # Explicit name allow-list blocks traversal, hidden files, and source media access.
    if not file_name or file_name != Path(file_name).name or "/" in file_name or "\\" in file_name:
        raise HTTPException(status_code=400, detail="invalid generated file name")
    location = _FILE_LOCATIONS.get(file_name)
    if location is None:
        raise HTTPException(status_code=404, detail="file not available")
    folder, filename = location
    candidate = (job_dir / folder / filename).resolve()
    try:
        candidate.relative_to(job_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid generated file path") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="file not available")
    return candidate


@router.get("/{job_id}/files/{file_name:path}")
def download_file(request: Request, job_id: str, file_name: str):
    store = request.app.state.store
    try:
        job_dir = store.job_dir(job_id)
        store.get(job_id)
    except (KeyError, StorageError) as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    candidate = resolve_generated_file(job_dir, file_name)
    media_type = "video/mp4" if candidate.suffix.lower() == ".mp4" else "application/octet-stream"
    if candidate.suffix.lower() in {".srt", ".vtt", ".ass"}:
        media_type = "text/plain; charset=utf-8"
    if candidate.suffix.lower() == ".json":
        media_type = "application/json"
    return FileResponse(candidate, media_type=media_type, filename=candidate.name)
