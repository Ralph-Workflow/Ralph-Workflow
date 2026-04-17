"""Merge integration handler for parallel worker branches.

Merges all SUCCEEDED worker branches into the base branch using git merge --no-ff.
Returns a MergeResult describing success or conflict outcome.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ralph.git.executor import GitExecutor
    from ralph.pipeline.worker_state import WorkerState

from ralph.pipeline.events import Event, PipelineEvent, WorkersMergeConflictEvent
from ralph.pipeline.worker_state import WorkerStatus


@dataclass(frozen=True)
class MergeResult:
    """Result of a merge integration operation.

    Attributes:
        success: True if all merges completed without conflicts.
        events: Pipeline events produced by the merge operation.
        conflicting_unit_ids: IDs of workers whose branches caused conflicts.
    """

    success: bool
    events: list[Event]
    conflicting_unit_ids: list[str] = field(default_factory=list)


async def integrate(
    base_branch: str,
    worker_states: Mapping[str, WorkerState],
    git_executor: GitExecutor,
    repo_root: Path,
) -> MergeResult:
    """Merge all SUCCEEDED worker branches into base_branch.

    For each SUCCEEDED worker in deterministic order (sorted by unit_id):
      - Run: git merge --no-ff ralph/unit-{unit_id}
      - On conflict: git merge --abort; record unit_id in conflicts list

    Args:
        base_branch: The branch to merge into (e.g. "main").
        worker_states: Mapping of unit_id to WorkerState.
        git_executor: Serialized executor for git operations.
        repo_root: Filesystem path to the git repository root.

    Returns:
        MergeResult with success=True and ALL_WORKERS_COMPLETE event on clean merge,
        or success=False and WorkersMergeConflictEvent on conflict.
    """
    succeeded_ids = sorted(
        unit_id for unit_id, ws in worker_states.items() if ws.status == WorkerStatus.SUCCEEDED
    )

    conflicting_unit_ids: list[str] = []

    for unit_id in succeeded_ids:
        branch_name = f"ralph/unit-{unit_id}"

        def _merge(branch: str = branch_name) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "merge", "--no-ff", branch],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

        result = await git_executor.arun(_merge)

        if result.returncode != 0:
            is_conflict = "CONFLICT" in result.stdout or "CONFLICT" in result.stderr
            if not is_conflict:
                raise RuntimeError(
                    f"git merge failed for branch {branch_name} "
                    f"(exit {result.returncode}): "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )

            def _abort() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            await git_executor.arun(_abort)
            conflicting_unit_ids.append(unit_id)

    if conflicting_unit_ids:
        return MergeResult(
            success=False,
            events=[WorkersMergeConflictEvent(conflicting_unit_ids=conflicting_unit_ids)],
            conflicting_unit_ids=conflicting_unit_ids,
        )

    return MergeResult(
        success=True,
        events=[PipelineEvent.ALL_WORKERS_COMPLETE],
        conflicting_unit_ids=[],
    )
