from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.pipeline import PipelineEvent, PipelineState, reduce
from ralph.pipeline.commit_executor import set_last_commit_error
from ralph.pipeline.reducer import DIRTY_WORKTREE_FAILURE_ENCODING
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect


def _commit_failure_policy() -> PipelinePolicy:
    return PipelinePolicy(
        entry_phase="development_commit",
        terminal_phase="failed_terminal",
        phases={
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
            "development_commit_review": PhaseDefinition(
                drain="development_commit_review",
                role="commit_review",
                transitions=PhaseTransition(on_success="development_commit"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="failed_terminal",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def _reduce_commit_failure(
    reason: str,
    policy: PipelinePolicy,
) -> tuple[PipelineState, list[Effect]]:
    set_last_commit_error(reason)
    try:
        return reduce(
            PipelineState(phase="development_commit"),
            PipelineEvent.COMMIT_FAILURE,
            policy,
        )
    finally:
        set_last_commit_error(None)


class TestDirtyWorktreeFailureEncoding:
    def test_constant_value_is_pinned(self) -> None:
        assert DIRTY_WORKTREE_FAILURE_ENCODING == "worktree not clean"

    def test_constant_is_lowercase_words_only(self) -> None:
        assert re.fullmatch(r"[a-z][a-z ]*[a-z]", DIRTY_WORKTREE_FAILURE_ENCODING)

    def test_dirty_tree_commit_failure_routes_to_automated_recovery(self) -> None:
        policy = _commit_failure_policy()
        reason = DIRTY_WORKTREE_FAILURE_ENCODING

        next_state, effects = _reduce_commit_failure(reason, policy)

        assert next_state.phase == "development_commit_review"
        assert next_state.last_error == reason
        assert next_state.pending_cycle_outcome is None
        assert effects == []

    def test_non_dirty_tree_commit_failure_routes_to_terminal_failure(self) -> None:
        policy = _commit_failure_policy()
        reason = "merge failed; see operator log for details"

        next_state, effects = _reduce_commit_failure(reason, policy)

        assert next_state.phase == policy.recovery.failed_route
        assert next_state.pending_cycle_outcome is None
        assert effects == []

    def test_encoding_matches_as_substring_of_canonical_deferral_reason(self) -> None:
        canonical = "worktree not clean; uncommitted tracked changes deferred catch-up integration"
        assert DIRTY_WORKTREE_FAILURE_ENCODING in canonical
