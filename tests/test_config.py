from backend.app.config import load_settings


def test_defaults_are_local_and_paid_api_free(monkeypatch, tmp_path):
    monkeypatch.setenv("YTKS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.default_model == "qwen3.5:4b"
    assert settings.max_batch_size == 20
    assert settings.translation_mode == "hybrid"
    assert settings.fast_model_dir == tmp_path / "models" / "opus-mt-tc-big-en-ko-ct2"
    assert settings.data_dir == tmp_path
