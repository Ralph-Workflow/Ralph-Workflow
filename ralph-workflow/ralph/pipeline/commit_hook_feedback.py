from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.publication_gate import publication_gate_loop_phase, write_loop_feedback

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.events import CommitHookRejected
    from ralph.policy.models import PipelinePolicy


def leave_commit_hook_feedback(
    root: Path, policy: PipelinePolicy, commit_phase: str, event: CommitHookRejected
) -> bool:
    loop_phase = publication_gate_loop_phase(policy, commit_phase)
    return loop_phase is not None and write_loop_feedback(
        root, policy, loop_phase, event.feedback()
    )


__all__ = ["leave_commit_hook_feedback"]
