"""Tests for serialized post-fanout workspace verification."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import PostFanoutVerificationEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path


def _minimal_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure=None,
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


_EXIT_CODE_VERIFY_FAIL = 2


def _make_scope(tmp_path: Path) -> WorkspaceScope:
    return WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))


class TestVerificationFailureMarksPhase:
    def test_post_fanout_verification_event_failure_enters_failed_recovery(self) -> None:
        """PostFanoutVerificationEvent(success=False) must route state to failed phase."""
        state = PipelineState(phase="development", worker_states={})
        event = PostFanoutVerificationEvent(
            success=False,
            exit_code=1,
            error="workspace verification failed (exit code 1)",
        )
        new_state, _ = reducer_reduce(state, event, _minimal_policy())
        assert new_state.phase == "failed_terminal"
        assert "workspace verification failed" in (new_state.last_error or "")

    def test_post_fanout_verification_event_success_is_noop(self) -> None:
        """PostFanoutVerificationEvent(success=True) must not change phase."""
        state = PipelineState(phase="development", worker_states={})
        event = PostFanoutVerificationEvent(success=True, exit_code=0)
        new_state, effects = reducer_reduce(state, event)
        assert new_state.phase == "development"
        assert effects == []

    def test_verification_failure_last_error_contains_message(self) -> None:
        """Phase failure state must carry the verification error in last_error."""
        state = PipelineState(phase="development", worker_states={})
        error_msg = "workspace verification failed (exit code 2): make: *** [verify] Error 2"
        event = PostFanoutVerificationEvent(success=False, exit_code=2, error=error_msg)
        new_state, _ = reducer_reduce(state, event, _minimal_policy())
        assert new_state.last_error is not None
        assert "workspace verification failed" in new_state.last_error
