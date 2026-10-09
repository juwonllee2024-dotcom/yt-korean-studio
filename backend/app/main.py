from __future__ import annotations

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.files import catalog_files, router as files_router
from .api.jobs import router as jobs_router
from .config import Settings, load_settings
from .media.youtube import YouTubeDownloader
from .pipeline.pipeline import Pipeline
from .providers.translation.ollama import TranslationProviderError, list_local_models
from .services.job_manager import JobStore


_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def _default_ollama_probe(base_url: str) -> bool:
    try:
        with httpx.Client(base_url=base_url, timeout=2.0) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def create_app(
    *,
    settings: Settings | None = None,
    store: JobStore | None = None,
    pipeline: Pipeline | None = None,
    youtube_downloader: YouTubeDownloader | None = None,
    model_lister: Callable[[], list[str]] | None = None,
    ollama_probe: Callable[[], bool] | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings()
    resolved_store = store or JobStore(resolved_settings.data_dir)
    resolved_pipeline = pipeline or Pipeline(resolved_store, settings=resolved_settings)
    resolved_youtube_downloader = youtube_downloader or YouTubeDownloader()
    resolved_model_lister = model_lister or (
        lambda: list_local_models(resolved_settings.ollama_base_url)
    )
    resolved_probe = ollama_probe or (
        lambda: _default_ollama_probe(resolved_settings.ollama_base_url)
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        application.state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="YT Korean Studio", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.store = resolved_store
    app.state.pipeline = resolved_pipeline
    app.state.youtube_downloader = resolved_youtube_downloader
    app.state.file_catalog = lambda job_id: catalog_files(resolved_store.job_dir(job_id))
    app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ytks-job")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{resolved_settings.port}",
            f"http://localhost:{resolved_settings.port}",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "ollama": {"reachable": bool(resolved_probe())}}

    @app.get("/api/models")
    def models() -> dict:
        try:
            names = resolved_model_lister()
        except (TranslationProviderError, httpx.HTTPError, OSError):
            names = []
        return {"models": names}

    app.include_router(jobs_router)
    app.include_router(files_router)

    if _FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(_FRONTEND_DIR / "index.html")

    return app


app = create_app()
