from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.media.youtube import DownloadedMedia
from backend.app.main import create_app
from backend.app.models.job import JobStatus
from backend.app.services.job_manager import JobStore


class FakePipeline:
    def __init__(self, store: JobStore):
        self.store = store
        self.run_ids: list[str] = []

    def run(self, job_id: str):
        self.run_ids.append(job_id)
        job = self.store.get(job_id)
        output = self.store.job_dir(job_id) / "output" / "final.mp4"
        output.write_bytes(b"final")
        return self.store.update(
            job_id,
            status=JobStatus.COMPLETED,
            current_stage="completed",
            progress=100,
        )

    def cancel(self, job_id: str):
        self.store.cancel(job_id)


class FakeYouTubeDownloader:
    def __init__(self, root: Path):
        self.root = root
        self.calls: list[str] = []

    def download(self, url: str, output_dir: Path) -> DownloadedMedia:
        self.calls.append(url)
        video = output_dir / "video.mp4"
        subtitle = output_dir / "video.en.srt"
        video.write_bytes(b"downloaded video")
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
            encoding="utf-8",
        )
        return DownloadedMedia(video_path=video, subtitle_path=subtitle)


def _make_client(
    tmp_path: Path,
    youtube_downloader: FakeYouTubeDownloader | None = None,
) -> tuple[TestClient, FakePipeline, JobStore]:
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "data",
        ollama_base_url="http://ollama.test",
        default_model="local-test",
        max_batch_size=20,
    )
    store = JobStore(settings.data_dir)
    pipeline = FakePipeline(store)
    app = create_app(
        settings=settings,
        store=store,
        pipeline=pipeline,
        youtube_downloader=youtube_downloader,
        model_lister=lambda: ["local-test", "qwen3.5:4b"],
        ollama_probe=lambda: True,
    )
    return TestClient(app), pipeline, store


def test_health_and_models_are_local(tmp_path):
    client, _, _ = _make_client(tmp_path)
    with client:
        assert client.get("/health").json() == {"ok": True, "ollama": {"reachable": True}}
        assert client.get("/api/models").json() == {"models": ["local-test", "qwen3.5:4b"]}


def test_create_job_copies_uploads_and_returns_allow_listed_files(tmp_path):
    client, pipeline, store = _make_client(tmp_path)
    with client:
        response = client.post(
            "/api/jobs",
            files={
                "video": ("source.mp4", b"video", "video/mp4"),
                "subtitle": (
                    "source.srt",
                    b"1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
                    "application/x-subrip",
                ),
            },
            data={"model": "local-test"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        job = store.get(job_id)
        assert Path(job.source_path).read_bytes() == b"video"
        assert pipeline.run_ids == [job_id]
        details = client.get(f"/api/jobs/{job_id}").json()
        assert details["status"] == "completed"
        assert "final.mp4" in details["files"]
        assert client.get(f"/api/jobs/{job_id}/files/final.mp4").content == b"final"


def test_create_job_from_youtube_url_requires_rights_and_reuses_pipeline(tmp_path):
    downloader = FakeYouTubeDownloader(tmp_path)
    client, pipeline, store = _make_client(tmp_path, downloader)
    url = "https://www.youtube.com/watch?v=abc123"
    with client:
        denied = client.post("/api/jobs", data={"youtube_url": url})
        assert denied.status_code == 400
        assert downloader.calls == []

        response = client.post(
            "/api/jobs",
            data={"youtube_url": url, "rights_confirmed": "true", "model": "local-test"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        assert downloader.calls == [url]
        for _ in range(20):
            details = client.get(f"/api/jobs/{job_id}").json()
            if details["status"] == "completed":
                break
        assert store.get(job_id).subtitle_path is not None
        assert pipeline.run_ids == [job_id]


def test_create_job_rejects_file_and_youtube_url_together(tmp_path):
    client, _, _ = _make_client(tmp_path)
    with client:
        response = client.post(
            "/api/jobs",
            files={"video": ("source.mp4", b"video", "video/mp4")},
            data={"youtube_url": "https://youtu.be/abc123", "rights_confirmed": "true"},
        )
        assert response.status_code == 400


def test_file_endpoint_rejects_path_traversal(tmp_path):
    client, _, _ = _make_client(tmp_path)
    with client:
        response = client.get("/api/jobs/job-1/files/..%2F..%2Fsecret.txt")
        assert response.status_code in {400, 404}


def test_cancel_endpoint_marks_job_cancelled(tmp_path):
    client, _, store = _make_client(tmp_path)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    subtitle = tmp_path / "source.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello.\n", encoding="utf-8")
    job = store.create(video, subtitle, None, model="local-test", job_id="cancel-me")
    with client:
        response = client.post(f"/api/jobs/{job.id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
