from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_REPO_ID = "Helsinki-NLP/opus-mt-tc-big-en-ko"
DEFAULT_MODEL_DIR = Path("data/models/opus-mt-tc-big-en-ko-ct2")
DEFAULT_TOKENIZER_DIR = Path("data/models/opus-mt-tc-big-en-ko-tokenizer")
MODEL_FILES = (
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "special_tokens_map.json",
    "source.spm",
    "target.spm",
    "tokenizer_config.json",
    "vocab.json",
)


def build_converter_command(
    *,
    converter: str,
    source_dir: Path,
    output_dir: Path,
) -> list[str]:
    """Build a reproducible INT8 CTranslate2 conversion command."""
    return [
        converter,
        "--model",
        str(source_dir),
        "--output_dir",
        str(output_dir),
        "--quantization",
        "int8",
        "--force",
    ]


def build_vocabulary_tokens(
    source_pieces: Sequence[str],
    target_vocab: Mapping[str, int],
) -> tuple[list[str], list[str]]:
    """Return source/target token lists in model ID order.

    This OPUS-MT checkpoint has separate SentencePiece vocabularies. The
    Transformers metadata points both directions at target ``vocab.json``,
    which silently turns many English source tokens into ``<unk>``. CTranslate2
    supports separate source and target vocabulary files, so preserve both.
    """
    source_tokens = list(source_pieces)
    if "<pad>" not in source_tokens:
        source_tokens.append("<pad>")
    ordered_target = sorted(target_vocab.items(), key=lambda item: int(item[1]))
    target_tokens = [token for token, _index in ordered_target]
    expected_ids = list(range(len(target_tokens)))
    actual_ids = [int(index) for _token, index in ordered_target]
    if actual_ids != expected_ids:
        raise RuntimeError("target vocabulary IDs must be contiguous and start at zero")
    if len(set(source_tokens)) != len(source_tokens):
        raise RuntimeError("source vocabulary contains duplicate tokens")
    if len(set(target_tokens)) != len(target_tokens):
        raise RuntimeError("target vocabulary contains duplicate tokens")
    return source_tokens, target_tokens


def ctranslate2_vocabulary_tokens(
    source_tokens: Sequence[str],
    target_tokens: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Remove Transformers' synthetic padding row before CT2 serialization."""

    def without_padding(tokens: Sequence[str]) -> list[str]:
        values = list(tokens)
        if "<pad>" not in values:
            return values
        if values[-1] != "<pad>":
            raise RuntimeError("<pad> must be the final vocabulary row")
        return values[:-1]

    return without_padding(source_tokens), without_padding(target_tokens)


def _download_snapshot(repo_id: str, tokenizer_dir: Path) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is missing; run .\\scripts\\install_fast_model.ps1"
        ) from exc
    tokenizer_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(tokenizer_dir),
        allow_patterns=list(MODEL_FILES),
    )


def _validate_snapshot(tokenizer_dir: Path) -> None:
    required = {"config.json", "model.safetensors", "source.spm", "target.spm", "vocab.json"}
    missing = sorted(name for name in required if not (tokenizer_dir / name).is_file())
    if missing:
        raise RuntimeError(
            f"downloaded model is missing required files in {tokenizer_dir}: {', '.join(missing)}"
        )


def _prepare_separate_vocabularies(tokenizer_dir: Path) -> tuple[list[str], list[str]]:
    try:
        import sentencepiece as spm
    except ImportError as exc:
        raise RuntimeError(
            "sentencepiece is missing; run .\\scripts\\install_fast_model.ps1"
        ) from exc
    source_processor = spm.SentencePieceProcessor(model_file=str(tokenizer_dir / "source.spm"))
    source_pieces = [source_processor.id_to_piece(index) for index in range(source_processor.vocab_size())]
    target_vocab = json.loads((tokenizer_dir / "vocab.json").read_text(encoding="utf-8"))
    if not isinstance(target_vocab, dict):
        raise RuntimeError("vocab.json must contain an object")
    source_tokens, target_tokens = build_vocabulary_tokens(source_pieces, target_vocab)

    source_vocab = {token: index for index, token in enumerate(source_tokens)}
    target_vocab_ordered = {token: index for index, token in enumerate(target_tokens)}
    # MarianTokenizer's legacy JSON loader uses the Windows locale, so escape
    # non-ASCII token strings while writing portable UTF-8 files.
    (tokenizer_dir / "source_vocab.json").write_text(
        json.dumps(source_vocab, ensure_ascii=True), encoding="utf-8"
    )
    (tokenizer_dir / "target_vocab.json").write_text(
        json.dumps(target_vocab_ordered, ensure_ascii=True), encoding="utf-8"
    )
    return source_tokens, target_tokens


def _convert_with_separate_vocabularies(
    source_dir: Path,
    output_dir: Path,
    source_tokens: Sequence[str],
    target_tokens: Sequence[str],
) -> None:
    try:
        from ctranslate2.converters.transformers import TransformersConverter
    except ImportError as exc:
        raise RuntimeError(
            "ctranslate2 and torch are required for model conversion; run .\\scripts\\install_fast_model.ps1"
        ) from exc

    converter = TransformersConverter(
        str(source_dir),
        copy_files=["source.spm", "target.spm"],
    )
    try:
        model_spec = converter._load()
        # This model stores independent English/Korean SentencePiece files.
        # TransformersConverter assumes one shared vocabulary, so correct the
        # two lists before validation and serialization.
        ct2_source_tokens, ct2_target_tokens = ctranslate2_vocabulary_tokens(
            source_tokens, target_tokens
        )
        model_spec._vocabularies["source"] = [ct2_source_tokens]
        model_spec._vocabularies["target"] = [ct2_target_tokens]
        model_spec.validate()
        model_spec.optimize(quantization="int8")
    except Exception as exc:
        raise RuntimeError(f"CTranslate2 model conversion failed: {exc}") from exc

    if output_dir.exists():
        if not output_dir.is_dir():
            raise RuntimeError(f"fast model output is not a directory: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_spec.save(str(output_dir))


def install_fast_model(
    *,
    repo_id: str = DEFAULT_REPO_ID,
    model_dir: Path = DEFAULT_MODEL_DIR,
    tokenizer_dir: Path = DEFAULT_TOKENIZER_DIR,
) -> None:
    model_dir = Path(model_dir).expanduser().resolve()
    tokenizer_dir = Path(tokenizer_dir).expanduser().resolve()
    print(f"Downloading {repo_id} files to {tokenizer_dir}")
    _download_snapshot(repo_id, tokenizer_dir)
    _validate_snapshot(tokenizer_dir)
    source_tokens, target_tokens = _prepare_separate_vocabularies(tokenizer_dir)

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    print("Converting to CPU INT8 CTranslate2 model")
    _convert_with_separate_vocabularies(tokenizer_dir, model_dir, source_tokens, target_tokens)
    print(f"Fast model ready: {model_dir}")
    print(f"Tokenizer files ready: {tokenizer_dir}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download OPUS-MT English-to-Korean files and convert them to CPU INT8."
    )
    parser.add_argument("--repo-id", default=os.getenv("YTKS_FAST_MODEL_REPO", DEFAULT_REPO_ID))
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(os.getenv("YTKS_FAST_MODEL_DIR", str(DEFAULT_MODEL_DIR))),
    )
    parser.add_argument(
        "--tokenizer-dir",
        type=Path,
        default=Path(os.getenv("YTKS_FAST_TOKENIZER_DIR", str(DEFAULT_TOKENIZER_DIR))),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        install_fast_model(
            repo_id=args.repo_id,
            model_dir=args.model_dir,
            tokenizer_dir=args.tokenizer_dir,
        )
    except (OSError, RuntimeError) as exc:
        print(f"Fast model setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
