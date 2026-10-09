from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from ...subtitles.types import SubtitleSegment, TranslatedSegment
from .ollama import TranslationProviderError


TokenizerFactory = Callable[[Path], Any]
TranslatorFactory = Callable[..., Any]


def _source_tokens_from_ids(tokenizer: Any, ids: Sequence[int]) -> list[str]:
    """Convert encoder IDs with source vocabulary when Marian uses split vocabs."""
    encoder = getattr(tokenizer, "encoder", None)
    if isinstance(encoder, dict):
        source_decoder: dict[int, str] = {}
        for token, index in encoder.items():
            try:
                source_decoder[int(index)] = str(token)
            except (TypeError, ValueError):
                continue
        if source_decoder:
            unknown = str(getattr(tokenizer, "unk_token", "<unk>"))
            return [source_decoder.get(int(index), unknown) for index in ids]
    converted = tokenizer.convert_ids_to_tokens(ids)
    return list(converted) if isinstance(converted, list) else [str(converted)]


def _target_ids_from_tokens(tokenizer: Any, tokens: Sequence[str]) -> list[int]:
    """Convert decoder tokens with target vocabulary for split-vocab Marian."""
    switch_target = getattr(tokenizer, "_switch_to_target_mode", None)
    switch_input = getattr(tokenizer, "_switch_to_input_mode", None)
    if callable(switch_target):
        switch_target()
    try:
        converted = tokenizer.convert_tokens_to_ids(list(tokens))
        if isinstance(converted, list):
            return [int(index) for index in converted]
        return [int(converted)]
    finally:
        if callable(switch_input):
            switch_input()


def _default_tokenizer_factory(model_dir: Path) -> Any:
    try:
        from transformers import AutoTokenizer, MarianTokenizer
    except ImportError as exc:
        raise TranslationProviderError(
            "fast translation needs transformers and sentencepiece; run install_fast_model.ps1"
        ) from exc
    try:
        source_vocab = model_dir / "source_vocab.json"
        target_vocab = model_dir / "target_vocab.json"
        if source_vocab.is_file() and target_vocab.is_file():
            return MarianTokenizer(
                source_spm=str(model_dir / "source.spm"),
                target_spm=str(model_dir / "target.spm"),
                vocab=str(source_vocab),
                target_vocab_file=str(target_vocab),
                source_lang="en",
                target_lang="ko",
                separate_vocabs=True,
            )
        return AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    except Exception as exc:
        raise TranslationProviderError(f"fast tokenizer is not ready: {exc}") from exc


def _default_translator_factory(model_dir: Path, **kwargs: Any) -> Any:
    try:
        import ctranslate2
    except ImportError as exc:
        raise TranslationProviderError(
            "fast translation needs ctranslate2; run install_fast_model.ps1"
        ) from exc
    try:
        return ctranslate2.Translator(str(model_dir), **kwargs)
    except Exception as exc:
        raise TranslationProviderError(f"fast translation model could not load: {exc}") from exc


class FastTranslationProvider:
    """Run a local OPUS-MT/CTranslate2 model for fast English-to-Korean batches."""

    def __init__(
        self,
        model_dir: Path,
        *,
        tokenizer_dir: Path | None = None,
        device: str = "cpu",
        compute_type: str = "int8_float32",
        inter_threads: int = 2,
        intra_threads: int = 0,
        tokenizer_factory: TokenizerFactory | None = None,
        translator_factory: TranslatorFactory | None = None,
    ) -> None:
        if inter_threads < 1:
            raise ValueError("inter_threads must be positive")
        if intra_threads < 0:
            raise ValueError("intra_threads cannot be negative")
        self.model_dir = Path(model_dir).expanduser().resolve()
        self.tokenizer_dir = Path(tokenizer_dir or model_dir).expanduser().resolve()
        self.device = device
        self.compute_type = compute_type
        self.inter_threads = inter_threads
        self.intra_threads = intra_threads
        self._tokenizer_factory = tokenizer_factory or _default_tokenizer_factory
        self._translator_factory = translator_factory or _default_translator_factory
        self._tokenizer: Any | None = None
        self._translator: Any | None = None

    def _ensure_loaded(self) -> tuple[Any, Any]:
        if self._tokenizer is not None and self._translator is not None:
            return self._tokenizer, self._translator
        if not self.model_dir.is_dir():
            raise TranslationProviderError(
                f"fast translation model folder is missing: {self.model_dir}"
            )
        self._tokenizer = self._tokenizer_factory(self.tokenizer_dir)
        translator_kwargs: dict[str, Any] = {
            "device": self.device,
            "compute_type": self.compute_type,
            "inter_threads": self.inter_threads,
        }
        if self.intra_threads:
            translator_kwargs["intra_threads"] = self.intra_threads
        self._translator = self._translator_factory(self.model_dir, **translator_kwargs)
        return self._tokenizer, self._translator

    def translate_batch(
        self,
        segments: Sequence[SubtitleSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[TranslatedSegment]:
        if not segments:
            return []
        if (source_lang, target_lang) != ("en", "ko"):
            raise TranslationProviderError("fast provider only supports English to Korean")
        try:
            tokenizer, translator = self._ensure_loaded()
            encoded = tokenizer(
                [segment.text for segment in segments],
                padding=False,
                truncation=True,
                return_tensors=None,
            )
            input_ids = encoded["input_ids"]
            token_batches = [_source_tokens_from_ids(tokenizer, ids) for ids in input_ids]
            results = translator.translate_batch(
                token_batches,
                beam_size=1,
                max_batch_size=len(token_batches),
            )
            if len(results) != len(segments):
                raise TranslationProviderError(
                    "fast translation returned a different number of cues"
                )
            translated: list[TranslatedSegment] = []
            for segment, result in zip(segments, results):
                hypotheses = getattr(result, "hypotheses", None)
                if not hypotheses or not hypotheses[0]:
                    raise TranslationProviderError(
                        f"fast translation returned empty text for ID {segment.id}"
                    )
                output_ids = _target_ids_from_tokens(tokenizer, hypotheses[0])
                korean_text = tokenizer.decode(output_ids, skip_special_tokens=True).strip()
                if not korean_text:
                    raise TranslationProviderError(
                        f"fast translation returned empty text for ID {segment.id}"
                    )
                translated.append(
                    TranslatedSegment(
                        id=segment.id,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        original_text=segment.text,
                        korean_text=korean_text,
                    )
                )
            return translated
        except TranslationProviderError:
            raise
        except Exception as exc:
            raise TranslationProviderError(f"fast translation failed: {exc}") from exc

    def close(self) -> None:
        self._tokenizer = None
        self._translator = None


CTranslate2TranslationProvider = FastTranslationProvider


__all__ = ["CTranslate2TranslationProvider", "FastTranslationProvider"]
