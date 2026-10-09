from pathlib import Path

from backend.app.benchmark import BenchmarkResult, run_benchmark
from backend.app.subtitles.types import SubtitleSegment, TranslatedSegment


class FakeProvider:
    def __init__(self, model: str):
        self.model = model

    def translate_batch(self, segments, source_lang, target_lang):
        return [
            TranslatedSegment(s.id, s.start_ms, s.end_ms, s.text, f"{self.model}:{s.text}")
            for s in segments
        ]


def test_benchmark_records_each_model_and_outputs(tmp_path):
    segments = [SubtitleSegment(1, 0, 1000, "Hello")]
    results = run_benchmark(
        segments,
        ["model-a", "model-b"],
        provider_factory=lambda model: FakeProvider(model),
        output_dir=tmp_path,
    )
    assert [result.model for result in results] == ["model-a", "model-b"]
    assert all(isinstance(result, BenchmarkResult) for result in results)
    output_files = list(tmp_path.glob("*.json"))
    assert len(output_files) == 1
