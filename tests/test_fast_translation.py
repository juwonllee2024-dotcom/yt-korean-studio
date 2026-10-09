from __future__ import annotations

from pathlib import Path

from backend.app.providers.translation.fast import (
    FastTranslationProvider,
    _source_tokens_from_ids,
    _target_ids_from_tokens,
)
from backend.app.subtitles.types import SubtitleSegment


class FakeTokenizer:
    def __call__(self, texts, **kwargs):
        assert kwargs["padding"] is False
        return {"input_ids": [[11 + index, 99] for index, _ in enumerate(texts)]}

    def convert_ids_to_tokens(self, ids):
        return [f"tok-{item}" for item in ids]

    def convert_tokens_to_ids(self, tokens):
        return [int(token.removeprefix("out-")) for token in tokens]

    def decode(self, ids, **kwargs):
        return "번역 " + str(ids[-1])


class FakeResult:
    def __init__(self, token_ids):
        self.hypotheses = [[f"out-{token_ids}"]]


class FakeTranslator:
    def __init__(self):
        self.calls: list[tuple[list[list[str]], dict[str, object]]] = []

    def translate_batch(self, tokens, **kwargs):
        self.calls.append((tokens, kwargs))
        return [FakeResult(index + 1) for index, _ in enumerate(tokens)]


def test_fast_provider_batches_tokens_and_preserves_timing(tmp_path: Path):
    translator = FakeTranslator()
    constructor_kwargs: dict[str, object] = {}

    def make_translator(_path, **kwargs):
        constructor_kwargs.update(kwargs)
        return translator

    provider = FastTranslationProvider(
        tmp_path,
        tokenizer_factory=lambda path: FakeTokenizer(),
        translator_factory=make_translator,
    )
    segments = [
        SubtitleSegment(1, 0, 1000, "Hello."),
        SubtitleSegment(2, 1000, 2400, "World."),
    ]

    result = provider.translate_batch(segments, "en", "ko")

    assert [item.id for item in result] == [1, 2]
    assert [item.start_ms for item in result] == [0, 1000]
    assert [item.end_ms for item in result] == [1000, 2400]
    assert [item.korean_text for item in result] == ["번역 1", "번역 2"]
    assert translator.calls[0][1]["beam_size"] == 1
    assert constructor_kwargs["compute_type"] == "int8_float32"


def test_separate_vocabulary_uses_source_tokens_for_encoder_input():
    class Tokenizer:
        encoder = {"<unk>": 0, "▁Hello": 1, "</s>": 2}
        unk_token = "<unk>"

    assert _source_tokens_from_ids(Tokenizer(), [1, 2]) == ["▁Hello", "</s>"]


def test_separate_vocabulary_uses_target_tokens_for_decoder_output():
    class Tokenizer:
        encoder = {"<unk>": 0, "▁Hello": 1}
        target_encoder = {"<unk>": 0, "▁안녕": 1}

        def _switch_to_target_mode(self):
            self.encoder = self.target_encoder

        def _switch_to_input_mode(self):
            self.encoder = {"<unk>": 0, "▁Hello": 1}

        def convert_tokens_to_ids(self, tokens):
            return [self.encoder.get(token, 0) for token in tokens]

    tokenizer = Tokenizer()
    assert _target_ids_from_tokens(tokenizer, ["▁안녕"]) == [1]
    assert tokenizer.encoder == {"<unk>": 0, "▁Hello": 1}
