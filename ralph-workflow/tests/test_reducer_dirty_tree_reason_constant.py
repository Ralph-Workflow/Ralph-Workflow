"""Public reducer behavior pins the dirty-tree commit-recovery encoding.

The commit executor exposes failure prose through ``set_last_commit_error``, and
the public ``reduce`` seam turns that prose into observable routing. These
tests pin the public ``DIRTY_WORKTREE_FAILURE_ENCODING`` constant and prove
that the encoded dirty-tree reason receives automated recovery while an
unrelated commit failure routes to terminal failure.
"""

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
    """Return a minimal policy with observable commit-recovery routing."""
    return PipelinePolicy(
        entry_phase="development_commit",
        terminal_phase="failed_terminal",
        phases={
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
            "development_commit_cleanup": PhaseDefinition(
                drain="development_commit_cleanup",
                role="commit_cleanup",
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
    """Reduce a commit failure carrying ``reason`` through the public seam."""
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
    """The dirty-tree routing input is one named constant, nothing else."""

    def test_constant_value_is_pinned(self) -> None:
        """The constant encodes exactly the canonical dirty-tree phrase."""
        assert DIRTY_WORKTREE_FAILURE_ENCODING == "worktree not clean"

    def test_constant_is_lowercase_words_only(self) -> None:
        """The encoding is plain lowercase prose (a substring, not a regex)."""
        assert re.fullmatch(r"[a-z][a-z ]*[a-z]", DIRTY_WORKTREE_FAILURE_ENCODING)

    def test_dirty_tree_commit_failure_routes_to_automated_recovery(self) -> None:
        """The encoded dirty-tree reason receives automated commit recovery."""
        policy = _commit_failure_policy()
        reason = DIRTY_WORKTREE_FAILURE_ENCODING

        next_state, effects = _reduce_commit_failure(reason, policy)

        assert next_state.phase == "development_commit_cleanup"
        assert next_state.last_error == reason
        assert next_state.pending_cycle_outcome is None
        assert effects == []

    def test_non_dirty_tree_commit_failure_routes_to_terminal_failure(self) -> None:
        """A commit failure without the dirty-tree encoding is not recovered."""
        policy = _commit_failure_policy()
        reason = "merge failed; see operator log for details"

        next_state, effects = _reduce_commit_failure(reason, policy)

        assert next_state.phase == policy.recovery.failed_route
        assert next_state.pending_cycle_outcome is None
        assert effects == []

    def test_encoding_matches_as_substring_of_canonical_deferral_reason(self) -> None:
        """The canonical deferral prose embeds the pinned encoding verbatim."""
        canonical = "worktree not clean; uncommitted tracked changes deferred catch-up integration"
        assert DIRTY_WORKTREE_FAILURE_ENCODING in canonical
