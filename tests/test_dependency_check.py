from scripts.check_dependencies import summarize


def test_dependency_report_has_safe_fields():
    report = summarize(command_lookup=lambda name: "present")
    assert set(report) == {"python", "ffmpeg", "ollama", "ollama_models"}
