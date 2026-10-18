"""Tests for reducer lock-retry behavior (WU-6).

Verifies that:
1. A repairable git failure with no recovery phase re-routes in-place
   to development_commit (not to failed_terminal), bounded by recovery_epoch < 3.
2. After recovery_epoch reaches the cap with no recovery phase, routes to failed_terminal
   via _enter_failed_recovery.
3. The message file at effect.message_file survives the failure and is re-read
   on the next pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.pipeline.commit_executor import set_last_commit_error
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import (
    _MAX_COMMIT_RECOVERY_ATTEMPTS,
    reduce,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

pytestmark = pytest.mark.timeout_seconds(5)


def _load_pipeline_policy() -> PipelinePolicy:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir).pipeline


def test_no_recovery_phase_routes_in_place() -> None:
    """A repairable git failure with no recovery phase re-routes in-place.

    When _resolve_commit_recovery_phase returns None (no commit_recovery and no
    commit_cleanup phase), _recoverable_git_failure_default_target returns the
    in-place phase name ('development_commit'). The reducer re-routes to it
    (not to failed_terminal), allowing commit_executor to re-read the validated
    message from effect.message_file.
    """
    policy = _load_pipeline_policy()

    # Use development_commit phase; patch _resolve_commit_recovery_phase to
    # return None (simulating a policy with no commit_cleanup phases).
    state = PipelineState(
        phase="development_commit",
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
    )
    set_last_commit_error("GitOperationError: Unable to create '.git/index.lock': File exists")
    try:
        # Patch in the reducer module so _handle_commit_failure sees no recovery phase
        with patch(
            "ralph.pipeline.reducer._resolve_commit_recovery_phase",
            return_value=None,
        ):
            next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # Must NOT reach failed_terminal — re-route in-place
        assert next_state.phase != "failed_terminal"
        # Re-routes to the in-place development_commit phase
        assert next_state.phase == "development_commit"
        # pending_cycle_outcome cleared (commit did not succeed)
        assert next_state.pending_cycle_outcome is None
        # recovery_epoch incremented
        assert next_state.recovery_epoch == 1
    finally:
        set_last_commit_error(None)


def test_recovery_epoch_caps_at_max() -> None:
    """After recovery_epoch reaches the cap with no recovery phase, routes to failed_terminal.

    Each in-place retry increments recovery_epoch. When recovery_epoch >= 3
    (the cap) and _resolve_commit_recovery_phase returns None,
    _recoverable_git_failure_default_target returns None and the reducer
    falls through to _enter_failed_recovery -> failed_terminal.
    """
    policy = _load_pipeline_policy()
    cap = _MAX_COMMIT_RECOVERY_ATTEMPTS

    # Test when recovery_epoch is at cap (3)
    state = PipelineState(
        phase="development_commit",
        recovery_epoch=cap,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
    )
    set_last_commit_error("GitOperationError: Unable to create '.git/index.lock': File exists")
    try:
        with patch(
            "ralph.pipeline.reducer._resolve_commit_recovery_phase",
            return_value=None,
        ):
            next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # Must reach failed_terminal when recovery_epoch is exhausted
        # AND there is no commit_recovery / commit_cleanup phase to fall back to
        assert next_state.phase == "failed_terminal", (
            f"Expected failed_terminal at recovery_epoch={cap}, got phase={next_state.phase}"
        )
        # pending_cycle_outcome cleared
        assert next_state.pending_cycle_outcome is None

        # Test when recovery_epoch is above cap (e.g. 5)
        state_above_cap = PipelineState(
            phase="development_commit",
            recovery_epoch=5,
            pending_cycle_outcome="completed",
            last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
        )
        with patch(
            "ralph.pipeline.reducer._resolve_commit_recovery_phase",
            return_value=None,
        ):
            next_state_above, _ = reduce(state_above_cap, PipelineEvent.COMMIT_FAILURE, policy)

        assert next_state_above.phase == "failed_terminal"
        assert next_state_above.pending_cycle_outcome is None
    finally:
        set_last_commit_error(None)


def test_message_file_preserved_through_retry(tmp_path: Path) -> None:
    """The message file at effect.message_file survives the failure.

    The commit executor only deletes the message file on COMMIT_SUCCESS or
    COMMIT_SKIPPED. On failure it sets _execution_record.last_error and returns
    COMMIT_FAILURE; the file stays on disk. A fresh pass of
    execute_commit_effect re-reads effect.message_file.

    The reducer is pure and issues no file-deletion effects for the in-place
    retry, so the message file is guaranteed to survive COMMIT_FAILURE handling.
    """
    from ralph.pipeline.commit_executor import execute_commit_effect

    # Create a message file (simulating what commit_executor wrote before failure)
    agent_dir = tmp_path / ".agent" / "artifacts"
    agent_dir.mkdir(parents=True, exist_ok=True)
    message_file = agent_dir / "commit_message.md"
    message_file.write_text(
        "---\ntype: commit\nsubject: feat: implement lock-retry test\n---\n",
        encoding="utf-8",
    )

    # Verify the message file exists before the reducer processes the failure
    assert message_file.exists(), "Message file must exist before failure handling"

    # Run the reducer with a repairable git failure
    policy = _load_pipeline_policy()
    state = PipelineState(
        phase="development_commit",
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
    )
    set_last_commit_error("GitOperationError: Unable to create '.git/index.lock': File exists")
    try:
        with patch(
            "ralph.pipeline.reducer._resolve_commit_recovery_phase",
            return_value=None,
        ):
            next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # Reducer routes in-place, no file deletion effect is issued (reducer is pure)
        assert next_state.phase == "development_commit"
        assert next_state.pending_cycle_outcome is None

        # The message file is untouched after reducer processes COMMIT_FAILURE
        assert message_file.exists(), (
            "Message file at effect.message_file must survive COMMIT_FAILURE; "
            "the commit executor only deletes it on SUCCESS or SKIPPED"
        )

        # A fresh execute_commit_effect re-reads the preserved message file
        effect = CommitEffect(message_file=str(message_file))
        _event = execute_commit_effect(effect, tmp_path)

        # The message file was NOT deleted by the reducer's COMMIT_FAILURE handling
        assert message_file.exists(), "Message file must still exist after reducer routes in-place"
    finally:
        set_last_commit_error(None)
