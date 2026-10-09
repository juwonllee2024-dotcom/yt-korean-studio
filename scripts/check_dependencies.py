from __future__ import annotations

import json
import platform
import shutil
from typing import Callable

import httpx


CommandLookup = Callable[[str], str | None]
Probe = Callable[[], bool]
ModelLookup = Callable[[], list[str]]


def _probe_ollama() -> bool:
    try:
        response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2.0)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def _models() -> list[str]:
    try:
        response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    names = [item.get("name") for item in payload.get("models", []) if isinstance(item, dict)]
    return [name for name in names if isinstance(name, str) and name]


def summarize(
    *,
    command_lookup: CommandLookup = shutil.which,
    ollama_probe: Probe | None = None,
    model_lookup: ModelLookup | None = None,
) -> dict[str, object]:
    """Return a read-only dependency report; never installs or downloads anything."""
    ollama_path = command_lookup("ollama")
    probe = ollama_probe or _probe_ollama
    lookup = model_lookup or _models
    return {
        "python": {"version": platform.python_version(), "executable": shutil.which("python")},
        "ffmpeg": {"path": command_lookup("ffmpeg"), "installed": command_lookup("ffmpeg") is not None},
        "ollama": {"path": ollama_path, "installed": ollama_path is not None, "reachable": bool(probe())},
        "ollama_models": lookup(),
    }


def main() -> int:
    print(json.dumps(summarize(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
