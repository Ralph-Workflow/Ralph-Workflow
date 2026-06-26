"""ProcessRunOptions — execution options for run_process and run_process_async."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@dataclass(frozen=True)
class ProcessRunOptions:
    """Execution options for run_process and run_process_async.

    Attributes:
        cwd: Working directory for the child (None = inherit).
        env: Extra environment variables (merged on top of ``os.environ``).
        timeout: Wall-clock bound on ``handle.communicate()`` (None = no bound).
        capture_output: When False, inherit stdout/stderr to the terminal.
        label: Per-call observability label recorded on the ProcessRecord.
            Defaults to ``"executor:run-process"`` inside ``run_process`` /
            ``run_process_async`` when None, so every child spawned through
            the executor is label-groupable in diagnostics
            (``pm.list_records(label_prefix=...)`` /
            ``pm.cleanup_orphans(label_prefix=...)``). The label does NOT
            change teardown — the child is still synchronously reaped on
            every code path (success / timeout / BaseException at
            ``process.py:175-194`` and ``:108-127``).
    """

    cwd: str | Path | None = None
    env: Mapping[str, str] | None = None
    timeout: float | None = None
    capture_output: bool = True
    label: str | None = None


__all__ = ["ProcessRunOptions"]
