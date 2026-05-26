"""_LegacyRunPipelineKwargs — TypedDict for backward-compatible run_pipeline kwargs."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.enums import Verbosity


class _LegacyRunPipelineKwargs(TypedDict, total=False):
    config_path: Path
    cli_overrides: dict[str, object]
    dry_run: bool
    resume: bool
    verbosity: Verbosity
    counter_overrides: dict[str, int]
    inline_prompt: str
    parallel_worker_manifest: Path | str


__all__ = ["_LegacyRunPipelineKwargs"]
