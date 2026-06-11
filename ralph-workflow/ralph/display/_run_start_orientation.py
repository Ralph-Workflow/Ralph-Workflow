"""Orientation data emitted at pipeline start.

Internal leaf module (wt-007-consolidate-display). Re-exports
:class:`RunStartOrientation` from the previous
``ralph.display.plain_renderer._run_start_orientation`` location so
``ParallelDisplay`` and ``ralph.pipeline.run_loop`` can import the data
class without taking a dependency on a renderer module.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RunStartOrientation:
    """Orientation data emitted once at pipeline start as a structured block."""

    prompt_path: str | None = None
    developer_agent: str | None = None
    developer_model: str | None = None
    developer_iters: int | None = None
    parallel_max_workers: int | None = None
    plan_present: bool = False
    verbosity: str | None = None
    workspace_root: str | None = None
    legend_enabled: bool = field(default=True)
