from pathlib import Path

from scripts.install_fast_model import (
    build_converter_command,
    build_vocabulary_tokens,
    ctranslate2_vocabulary_tokens,
)


def test_converter_command_uses_local_opus_model_and_int8(tmp_path: Path) -> None:
    command = build_converter_command(
        converter="ct2-transformers-converter",
        source_dir=tmp_path / "source",
        output_dir=tmp_path / "ct2",
    )

    assert command[:2] == ["ct2-transformers-converter", "--model"]
    assert str(tmp_path / "source") in command
    assert "--output_dir" in command
    assert str(tmp_path / "ct2") in command
    assert command[command.index("--quantization") + 1] == "int8"
    assert "--force" in command


def test_separate_vocabulary_tokens_keep_source_and_target_id_order() -> None:
    source_tokens, target_tokens = build_vocabulary_tokens(
        ["<unk>", "<s>", "</s>", "▁Hello"],
        {"<unk>": 0, "<s>": 1, "</s>": 2, "▁안녕": 3, "<pad>": 4},
    )

    assert source_tokens == ["<unk>", "<s>", "</s>", "▁Hello", "<pad>"]
    assert target_tokens == ["<unk>", "<s>", "</s>", "▁안녕", "<pad>"]


def test_ctranslate2_vocabulary_excludes_transformers_padding_row() -> None:
    source, target = ctranslate2_vocabulary_tokens(
        ["<unk>", "<s>", "</s>", "▁Hello", "<pad>"],
        ["<unk>", "<s>", "</s>", "▁안녕", "<pad>"],
    )

    assert source == ["<unk>", "<s>", "</s>", "▁Hello"]
    assert target == ["<unk>", "<s>", "</s>", "▁안녕"]
