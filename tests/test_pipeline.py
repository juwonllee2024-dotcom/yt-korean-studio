from pathlib import Path

import pytest

from backend.app.models.job import JobStatus
from backend.app.providers.translation.ollama import TranslationProviderError
from backend.app.providers.translation.fast import FastTranslationProvider
from backend.app.providers.translation.hybrid import HybridTranslationProvider
from backend.app.providers.translation.ollama import OllamaTranslationProvider
from backend.app.services.job_manager import JobStore
from backend.app.pipeline.pipeline import Pipeline, PipelineError
from backend.app.subtitles.srt_io import parse_subtitles
from backend.app.subtitles.types import TranslatedSegment
from backend.app.config import Settings


class FakeProvider:
    def __init__(self, calls: list[int]):
        self.calls = calls

    def translate_batch(self, segments, source_lang, target_lang):
        self.calls.append(len(segments))
        return [
            TranslatedSegment(
                id=segment.id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                original_text=segment.text,
                korean_text=f"한국어: {segment.text}",
            )
            for segment in segments
        ]


class BatchLimitedProvider(FakeProvider):
    def translate_batch(self, segments, source_lang, target_lang):
        if len(segments) > 1:
            self.calls.append(len(segments))
            raise TranslationProviderError("model omitted a cue")
        return super().translate_batch(segments, source_lang, target_lang)


def _job(tmp_path: Path, job_id: str = "job-1"):
    video = tmp_path / f"{job_id}.mp4"
    subtitle = tmp_path / f"{job_id}.srt"
    video.write_bytes(b"video")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld.\n",
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "data")
    store.create(video, subtitle, None, model="local-test", job_id=job_id)
    return store


def _renderer(*args):
    output = Path(args[-1])
    output.write_bytes(b"subtitled")


def test_pipeline_reuses_existing_translation_on_restart(tmp_path):
    store = _job(tmp_path)
    calls: list[int] = []
    first = Pipeline(
        store,
        provider_factory=lambda model: FakeProvider(calls),
        render_fn=_renderer,
    )
    completed = first.run("job-1")
    assert completed.status is JobStatus.COMPLETED
    assert calls == [2]

    second = Pipeline(
        store,
        provider_factory=lambda model: FakeProvider(calls),
        render_fn=_renderer,
    )
    resumed = second.run("job-1")
    assert resumed.status is JobStatus.COMPLETED
    assert calls == [2]


def test_pipeline_keeps_prior_outputs_when_render_fails(tmp_path):
    store = _job(tmp_path, "job-2")
    calls: list[int] = []

    def failing_renderer(*_args):
        raise PipelineError("simulated render failure")

    failed = Pipeline(
        store,
        provider_factory=lambda model: FakeProvider(calls),
        render_fn=failing_renderer,
    ).run("job-2")
    job_dir = tmp_path / "data" / "jobs" / "job-2"
    assert failed.status is JobStatus.FAILED
    assert "simulated render failure" in (failed.error or "")
    assert (job_dir / "subtitles" / "korean.srt").exists()
    assert (job_dir / "subtitles" / "korean.ass").exists()
    assert calls == [2]


def test_failed_pipeline_resumes_without_retranslating(tmp_path):
    store = _job(tmp_path, "job-3")
    calls: list[int] = []

    def failing_renderer(_args):
        raise PipelineError("first render failed")

    Pipeline(
        store,
        provider_factory=lambda model: FakeProvider(calls),
        render_fn=failing_renderer,
    ).run("job-3")

    resumed = Pipeline(
        store,
        provider_factory=lambda model: FakeProvider(calls),
        render_fn=_renderer,
    ).run("job-3")
    assert resumed.status is JobStatus.COMPLETED
    assert calls == [2]


def test_pipeline_retries_a_malformed_large_batch_as_single_cues(tmp_path):
    store = _job(tmp_path, "job-4")
    calls: list[int] = []
    result = Pipeline(
        store,
        provider_factory=lambda model: BatchLimitedProvider(calls),
        render_fn=_renderer,
    ).run("job-4")
    assert result.status is JobStatus.COMPLETED
    assert calls == [2, 1, 1]


def test_pipeline_writes_pyconkr_subtitle_qa_report(tmp_path):
    store = _job(tmp_path, "job-qa")
    result = Pipeline(
        store,
        provider_factory=lambda model: FakeProvider([]),
        render_fn=_renderer,
    ).run("job-qa")

    assert result.status is JobStatus.COMPLETED
    qa_path = tmp_path / "data" / "jobs" / "job-qa" / "subtitles" / "qa.json"
    assert qa_path.exists()
    payload = qa_path.read_text(encoding="utf-8")
    assert '"total_cues": 2' in payload
    assert '"passed": true' in payload


def test_pipeline_formats_overlapping_cues_without_simultaneous_display(tmp_path):
    store = _job(tmp_path, "job-overlap")
    subtitle_path = store.get("job-overlap").subtitle_path
    assert subtitle_path is not None
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:03,360\nOne.\n\n"
        "2\n00:00:01,160 --> 00:00:05,160\nTwo.\n",
        encoding="utf-8",
    )

    result = Pipeline(
        store,
        provider_factory=lambda model: FakeProvider([]),
        render_fn=_renderer,
    ).run("job-overlap")

    assert result.status is JobStatus.COMPLETED
    korean_path = tmp_path / "data" / "jobs" / "job-overlap" / "subtitles" / "korean.srt"
    formatted = parse_subtitles(korean_path)
    assert [(segment.start_ms, segment.end_ms) for segment in formatted] == [
        (0, 1160),
        (1160, 5160),
    ]


def test_pipeline_default_provider_uses_hybrid_mode_when_configured(tmp_path):
    store = _job(tmp_path, "job-hybrid")
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "data",
        ollama_base_url="http://ollama.test",
        default_model="qwen3.5:4b",
        max_batch_size=20,
        translation_mode="hybrid",
        fast_model_dir=tmp_path / "fast-model",
        fast_tokenizer_dir=tmp_path / "fast-tokenizer",
    )

    pipeline = Pipeline(store, settings=settings, render_fn=_renderer)
    provider = pipeline.provider_factory("qwen3.5:4b")

    assert isinstance(provider, HybridTranslationProvider)
    assert isinstance(provider.fast_provider, FastTranslationProvider)
    assert isinstance(provider.review_provider, OllamaTranslationProvider)
    provider.close()


def test_pipeline_translation_cache_changes_when_mode_changes(tmp_path):
    store = _job(tmp_path, "job-mode-cache")
    calls: list[int] = []

    def fail_renderer(*_args):
        raise PipelineError("stop after translation")

    base = dict(
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "data",
        ollama_base_url="http://ollama.test",
        default_model="local-test",
        max_batch_size=20,
        fast_model_dir=tmp_path / "fast-model",
        fast_tokenizer_dir=tmp_path / "fast-tokenizer",
    )
    first = Pipeline(
        store,
        settings=Settings(**base, translation_mode="ollama"),
        provider_factory=lambda model: FakeProvider(calls),
        render_fn=fail_renderer,
    )
    assert first.run("job-mode-cache").status is JobStatus.FAILED

    second = Pipeline(
        store,
        settings=Settings(**base, translation_mode="hybrid"),
        provider_factory=lambda model: FakeProvider(calls),
        render_fn=_renderer,
    )
    assert second.run("job-mode-cache").status is JobStatus.COMPLETED
    assert calls == [2, 2]
