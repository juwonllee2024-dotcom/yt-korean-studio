from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .providers.translation.base import TranslationProvider
from .subtitles.types import SubtitleSegment, TranslatedSegment


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    model: str
    elapsed_s: float
    success: bool
    outputs: list[dict[str, object]]
    error: str | None = None


def _output_rows(segments: Sequence[TranslatedSegment]) -> list[dict[str, object]]:
    return [
        {
            "id": segment.id,
            "original_text": segment.original_text,
            "korean_text": segment.korean_text,
        }
        for segment in segments
    ]


def run_benchmark(
    segments: Sequence[SubtitleSegment],
    models: Sequence[str],
    *,
    provider_factory: Callable[[str], TranslationProvider],
    output_dir: Path,
    source_lang: str = "en",
    target_lang: str = "ko",
) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    for model in models:
        started = time.perf_counter()
        try:
            provider = provider_factory(model)
            translated = provider.translate_batch(segments, source_lang, target_lang)
            result = BenchmarkResult(
                model=model,
                elapsed_s=round(time.perf_counter() - started, 3),
                success=True,
                outputs=_output_rows(translated),
            )
        except Exception as exc:  # benchmark records failures so another model can run
            result = BenchmarkResult(
                model=model,
                elapsed_s=round(time.perf_counter() - started, 3),
                success=False,
                outputs=[],
                error=str(exc),
            )
        results.append(result)

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    (output_dir / f"{stamp}.json").write_text(
        json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results
