from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    ollama_base_url: str
    default_model: str
    max_batch_size: int
    translation_mode: str = "hybrid"
    fast_model_dir: Path = Path("data/models/opus-mt-tc-big-en-ko-ct2")
    fast_tokenizer_dir: Path = Path("data/models/opus-mt-tc-big-en-ko-tokenizer")


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def load_settings() -> Settings:
    batch = min(_positive_int("YTKS_MAX_BATCH_SIZE", 20), 20)
    data_dir = Path(os.getenv("YTKS_DATA_DIR", "data"))
    mode = os.getenv("YTKS_TRANSLATION_MODE", "hybrid").strip().lower()
    if mode not in {"ollama", "hybrid"}:
        raise ValueError("YTKS_TRANSLATION_MODE must be 'ollama' or 'hybrid'")
    return Settings(
        host=os.getenv("YTKS_HOST", "127.0.0.1"),
        port=_positive_int("YTKS_PORT", 8000),
        data_dir=data_dir,
        ollama_base_url=os.getenv("YTKS_OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        default_model=os.getenv("YTKS_DEFAULT_MODEL", "qwen3.5:4b"),
        max_batch_size=batch,
        translation_mode=mode,
        fast_model_dir=Path(
            os.getenv(
                "YTKS_FAST_MODEL_DIR",
                str(data_dir / "models" / "opus-mt-tc-big-en-ko-ct2"),
            )
        ),
        fast_tokenizer_dir=Path(
            os.getenv(
                "YTKS_FAST_TOKENIZER_DIR",
                str(data_dir / "models" / "opus-mt-tc-big-en-ko-tokenizer"),
            )
        ),
    )
