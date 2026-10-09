from __future__ import annotations

from typing import Sequence

from .renderer import CommandRunner, MediaError


def run_ffmpeg(args: Sequence[str], *, runner: CommandRunner | None = None) -> None:
    """Run a fixed FFmpeg argument list through the renderer's safe command boundary."""
    if runner is None:
        from .renderer import _run_command

        runner = _run_command
    runner(args)


__all__ = ["CommandRunner", "MediaError", "run_ffmpeg"]
