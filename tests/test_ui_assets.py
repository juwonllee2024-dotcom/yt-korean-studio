from pathlib import Path


def test_ui_contains_no_upload_to_youtube_action():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "Create Video" in html
    assert "Upload to YouTube" not in html


def test_ui_has_required_local_inputs_and_progress_hooks():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'id="video-input"' in html
    assert 'id="subtitle-input"' in html
    assert 'id="intro-input"' in html
    assert 'id="youtube-url"' in html
    assert 'id="rights-confirmed"' in html
    assert 'id="model-input"' in html
    assert 'id="create-button"' in html
    assert 'id="progress-card"' in html
    assert "하이브리드" in html
    javascript = Path("frontend/app.js").read_text(encoding="utf-8")
    assert "/api/models" in javascript
    assert "/api/jobs" in javascript
