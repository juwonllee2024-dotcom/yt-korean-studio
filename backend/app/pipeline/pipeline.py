from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable, Sequence

from ..config import Settings, load_settings
from ..media.renderer import concat_intro, render_subtitles
from ..models.job import Job, JobStatus
from ..providers.translation.fast import FastTranslationProvider
from ..providers.translation.base import TranslationProvider
from ..providers.translation.hybrid import HybridTranslationProvider
from ..providers.translation.ollama import OllamaTranslationProvider, TranslationProviderError
from ..services.job_manager import JobStore
from ..subtitles.qa import check_pyconkr_guidelines, normalize_segments_for_display
from ..subtitles.srt_io import parse_subtitles
from ..subtitles.translated_io import SubtitleStyle, write_ass, write_translated_srt
from ..subtitles.types import SubtitleSegment, TranslatedSegment


class PipelineError(RuntimeError):
    """Raised when a pipeline stage cannot complete."""


ProviderFactory = Callable[[str], TranslationProvider]
MediaFunction = Callable[..., None]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PipelineError(f"cannot read input: {path.name}") from exc
    return digest.hexdigest()


def _hash_parts(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _path_state(path: Path) -> str:
    """Return a cheap cache marker for an optional local model path."""
    try:
        stat = path.stat()
    except OSError:
        return f"{path}:missing"
    return f"{path}:{stat.st_mtime_ns}:{stat.st_size}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_segments(path: Path) -> list[TranslatedSegment]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_segments = payload["segments"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"invalid transcript: {path.name}") from exc
    if not isinstance(raw_segments, list):
        raise PipelineError("transcript segments must be an array")
    result: list[TranslatedSegment] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise PipelineError("transcript contains an invalid segment")
        try:
            result.append(
                TranslatedSegment(
                    id=int(raw["id"]),
                    start_ms=int(raw["start_ms"]),
                    end_ms=int(raw["end_ms"]),
                    original_text=str(raw["original_text"]),
                    korean_text=str(raw["korean_text"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineError("transcript contains an incomplete segment") from exc
    if not result:
        raise PipelineError("subtitle file contains no cues")
    return result


class Pipeline:
    """Idempotent subtitle translation/render pipeline."""

    _PROGRESS = {
        "parsing": 20.0,
        "translating": 50.0,
        "formatting": 65.0,
        "rendering": 85.0,
        "concatenating": 99.0,
    }

    def __init__(
        self,
        store: JobStore,
        *,
        provider_factory: ProviderFactory | None = None,
        render_fn: MediaFunction = render_subtitles,
        concat_fn: MediaFunction = concat_intro,
        max_batch_size: int | None = None,
        settings: Settings | None = None,
        source_lang: str = "en",
        target_lang: str = "ko",
    ) -> None:
        self.store = store
        resolved_settings = settings or load_settings()
        if provider_factory is not None:
            self.provider_factory = provider_factory
        elif resolved_settings.translation_mode == "hybrid":
            self.provider_factory = lambda model: HybridTranslationProvider(
                fast_provider=FastTranslationProvider(
                    resolved_settings.fast_model_dir,
                    tokenizer_dir=resolved_settings.fast_tokenizer_dir,
                ),
                review_provider=OllamaTranslationProvider(
                    resolved_settings.ollama_base_url,
                    model,
                ),
            )
        else:
            self.provider_factory = lambda model: OllamaTranslationProvider(
                resolved_settings.ollama_base_url,
                model,
            )
        self.render_fn = render_fn
        self.concat_fn = concat_fn
        self.max_batch_size = max(1, min(max_batch_size or resolved_settings.max_batch_size, 20))
        self.translation_mode = resolved_settings.translation_mode
        self.fast_model_dir = resolved_settings.fast_model_dir
        self.fast_tokenizer_dir = resolved_settings.fast_tokenizer_dir
        self.source_lang = source_lang
        self.target_lang = target_lang

    def _log(self, job_id: str, message: str) -> None:
        log_path = self.store.job_dir(job_id) / "logs" / "job.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")

    def _set_stage(self, job_id: str, stage: str) -> None:
        self.store.update(
            job_id,
            status=JobStatus(stage),
            current_stage=stage,
            progress=self._PROGRESS[stage],
            error=None,
        )
        self._log(job_id, f"stage: {stage}")

    def _cancelled(self, job_id: str) -> bool:
        return self.store.get(job_id).status is JobStatus.CANCELLED

    def _cached(self, job: Job, stage: str, fingerprint: str, outputs: Sequence[Path]) -> bool:
        record = job.stage_state.get(stage)
        if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
            return False
        return all(Path(output).is_file() for output in outputs)

    @staticmethod
    def _parsed_payload(segments: Sequence[SubtitleSegment]) -> dict[str, Any]:
        return {
            "source_lang": "en",
            "target_lang": "ko",
            "translated": False,
            "segments": [
                {
                    "id": segment.id,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "original_text": segment.text,
                }
                for segment in segments
            ],
        }

    @staticmethod
    def _translated_payload(segments: Sequence[TranslatedSegment]) -> dict[str, Any]:
        return {
            "source_lang": "en",
            "target_lang": "ko",
            "translated": True,
            "segments": [
                {
                    "id": segment.id,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "original_text": segment.original_text,
                    "korean_text": segment.korean_text,
                }
                for segment in segments
            ],
        }

    def _write_metadata(self, job: Job) -> None:
        metadata = {
            "job_id": job.id,
            "model": job.model,
            "translation_mode": self.translation_mode,
            "fast_model_dir": str(self.fast_model_dir) if self.translation_mode == "hybrid" else None,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "source_file": job.source_path.name,
            "subtitle_file": job.subtitle_path.name if job.subtitle_path else None,
            "intro_file": job.intro_path.name if job.intro_path else None,
        }
        _write_json(self.store.job_dir(job.id) / "metadata.json", metadata)

    def _parse(self, job: Job, transcript: Path) -> None:
        if job.subtitle_path is None:
            raise PipelineError("English subtitle file is required for MVP")
        fingerprint = _hash_file(job.subtitle_path)
        if self._cached(job, "parsing", fingerprint, [transcript]):
            return
        segments = parse_subtitles(job.subtitle_path)
        if not segments:
            raise PipelineError("subtitle file contains no cues")
        _write_json(transcript, self._parsed_payload(segments))
        self.store.set_stage_state(job.id, "parsing", fingerprint=fingerprint, outputs=[transcript])

    def _translate(self, job: Job, transcript: Path) -> None:
        if job.subtitle_path is None:
            raise PipelineError("English subtitle file is required for MVP")
        fingerprint = _hash_parts(
            _hash_file(job.subtitle_path),
            job.model,
            self.source_lang,
            self.target_lang,
            self.translation_mode,
            _path_state(self.fast_model_dir),
            _path_state(self.fast_tokenizer_dir),
        )
        if self._cached(job, "translating", fingerprint, [transcript]):
            return
        original_segments = parse_subtitles(job.subtitle_path)
        provider = self.provider_factory(job.model)
        translated: list[TranslatedSegment] = []
        try:
            for start in range(0, len(original_segments), self.max_batch_size):
                batch = original_segments[start : start + self.max_batch_size]
                try:
                    translated.extend(provider.translate_batch(batch, self.source_lang, self.target_lang))
                except TranslationProviderError:
                    # Smaller retries recover from local models that obey JSON but omit a cue in a large batch.
                    if len(batch) == 1:
                        raise
                    for segment in batch:
                        translated.extend(
                            provider.translate_batch([segment], self.source_lang, self.target_lang)
                        )
        except Exception as exc:
            if isinstance(exc, PipelineError):
                raise
            raise PipelineError(f"translation failed: {exc}") from exc
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        if len(translated) != len(original_segments):
            raise PipelineError("translation returned a different number of cues")
        _write_json(transcript, self._translated_payload(translated))
        self.store.set_stage_state(job.id, "translating", fingerprint=fingerprint, outputs=[transcript])

    def _format(
        self,
        job: Job,
        transcript: Path,
        korean_srt: Path,
        korean_ass: Path,
        qa_report_path: Path,
    ) -> None:
        fingerprint = _hash_parts(_hash_file(transcript), "style-v4", "pyconkr-qa-v1")
        if self._cached(job, "formatting", fingerprint, [korean_srt, korean_ass, qa_report_path]):
            return
        translated = _read_segments(transcript)
        display_segments = normalize_segments_for_display(translated)
        qa_report = check_pyconkr_guidelines(display_segments)
        style = SubtitleStyle()
        write_translated_srt(display_segments, korean_srt)
        write_ass(display_segments, korean_ass, style)
        _write_json(qa_report_path, qa_report.as_dict())
        if not qa_report.passed:
            self._log(job.id, f"subtitle QA: {len(qa_report.issues)} guideline warning(s)")
        self.store.set_stage_state(
            job.id,
            "formatting",
            fingerprint=fingerprint,
            outputs=[korean_srt, korean_ass, qa_report_path],
        )

    def _render(self, job: Job, korean_ass: Path, subtitled: Path) -> None:
        fingerprint = _hash_parts(_hash_file(job.source_path), _hash_file(korean_ass), "render-v1")
        if self._cached(job, "rendering", fingerprint, [subtitled]):
            return
        self.render_fn(job.source_path, korean_ass, subtitled)
        self.store.set_stage_state(job.id, "rendering", fingerprint=fingerprint, outputs=[subtitled])

    def _concat(self, job: Job, subtitled: Path, final: Path) -> None:
        intro_hash = _hash_file(job.intro_path) if job.intro_path else "no-intro"
        fingerprint = _hash_parts(_hash_file(subtitled), intro_hash, "final-v1")
        if self._cached(job, "concatenating", fingerprint, [final]):
            return
        if job.intro_path:
            self.concat_fn(job.intro_path, subtitled, final)
        else:
            final.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(subtitled, final)
        self.store.set_stage_state(job.id, "concatenating", fingerprint=fingerprint, outputs=[final])

    def run(self, job_id: str) -> Job:
        job = self.store.get(job_id)
        if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
            return job
        current_stage = "queued"
        try:
            self._write_metadata(job)
            transcript = self.store.job_dir(job_id) / "transcript.json"
            korean_srt = self.store.job_dir(job_id) / "subtitles" / "korean.srt"
            korean_ass = self.store.job_dir(job_id) / "subtitles" / "korean.ass"
            qa_report = self.store.job_dir(job_id) / "subtitles" / "qa.json"
            subtitled = self.store.job_dir(job_id) / "output" / "subtitled.mp4"
            final = self.store.job_dir(job_id) / "output" / "final.mp4"

            current_stage = "parsing"
            self._set_stage(job_id, current_stage)
            self._parse(job, transcript)
            if self._cancelled(job_id):
                return self.store.get(job_id)

            current_stage = "translating"
            self._set_stage(job_id, current_stage)
            self._translate(job, transcript)
            if self._cancelled(job_id):
                return self.store.get(job_id)

            current_stage = "formatting"
            self._set_stage(job_id, current_stage)
            self._format(job, transcript, korean_srt, korean_ass, qa_report)
            if self._cancelled(job_id):
                return self.store.get(job_id)

            current_stage = "rendering"
            self._set_stage(job_id, current_stage)
            self._render(job, korean_ass, subtitled)
            if self._cancelled(job_id):
                return self.store.get(job_id)

            current_stage = "concatenating"
            self._set_stage(job_id, current_stage)
            self._concat(job, subtitled, final)
            if self._cancelled(job_id):
                return self.store.get(job_id)

            self.store.update(
                job_id,
                status=JobStatus.COMPLETED,
                current_stage="completed",
                progress=100.0,
                error=None,
            )
            self._log(job_id, "completed")
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            self.store.update(
                job_id,
                status=JobStatus.FAILED,
                current_stage=current_stage,
                error=message[:500],
            )
            self._log(job_id, f"failed: {message}")
        return self.store.get(job_id)

    def cancel(self, job_id: str) -> None:
        self.store.cancel(job_id)
