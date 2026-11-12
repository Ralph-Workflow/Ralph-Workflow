from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunStartOrientation:
    prompt_path: str | None = None
    developer_agent: str | None = None
    developer_model: str | None = None
    developer_iters: int | None = None
    parallel_max_workers: int | None = None
    plan_present: bool = False
    verbosity: str | None = None
    workspace_root: str | None = None
    target_worktree_path: str | None = None
