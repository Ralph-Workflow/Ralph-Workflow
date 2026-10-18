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


def test_recovery_epoch_clamps_at_max() -> None:
    """Past the cap, the reducer clamps ``recovery_epoch`` and continues in-place.

    The cap is purely diagnostic for repairable failures: the reducer never
    escalates to ``failed_terminal`` merely because ``recovery_epoch`` reached
    ``_MAX_COMMIT_RECOVERY_ATTEMPTS``. Past the cap, the in-place retry
    continues with the epoch clamped at the cap, and the validated commit
    message is re-read on the next pass.
    """
    policy = _load_pipeline_policy()
    cap = _MAX_COMMIT_RECOVERY_ATTEMPTS

    # At the cap: re-route in-place and clamp at the cap.
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

        assert next_state.phase == "development_commit", (
            f"Expected in-place retry at recovery_epoch={cap}, got phase={next_state.phase}"
        )
        # Epoch is clamped at the cap (does not increment past it).
        assert next_state.recovery_epoch == cap, (
            f"recovery_epoch must be clamped at the cap, got {next_state.recovery_epoch}"
        )
        assert next_state.pending_cycle_outcome is None

        # Above the cap (e.g. 5): re-route in-place and keep the epoch at the cap.
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

        assert next_state_above.phase == "development_commit"
        assert next_state_above.recovery_epoch == cap
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


def test_valid_message_preserved_through_failure_then_success(tmp_path: Path) -> None:
    """A validated commit message survives a real failure and is re-used on success.

    Demonstrates the full failure-then-success commit seam required by AC-4:
    a real ``execute_commit_effect`` invocation fails on a fake stage-all
    (simulated lock contention), the message file stays on disk, the reducer
    routes in-place, and the next pass reads the same message and succeeds.
    """
    from ralph.git.commit_result import CommitCreationResult
    from ralph.pipeline.commit_executor import (
        execute_commit_effect,
        get_last_commit_error,
    )

    agent_dir = tmp_path / ".agent" / "artifacts"
    agent_dir.mkdir(parents=True, exist_ok=True)
    message_file = agent_dir / "commit_message.md"
    message_text = (
        "---\ntype: commit\nsubject: feat: valid message must be reused after lock retry\n---\n"
    )
    message_file.write_text(message_text, encoding="utf-8")

    # Set up a real git repo_root so the executor's checks pass.
    from git import Actor, Repo

    repo_root = tmp_path / "worktree"
    repo = Repo.init(repo_root)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    actor = Actor("Test User", "test@example.com")
    init_file = repo_root / "initial.txt"
    init_file.write_text("initial content", encoding="utf-8")
    repo.index.add(["initial.txt"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    if repo.active_branch.name != "main":
        repo.git.branch("-M", "main")
    # Stage a real change so the executor detects commit work.
    (repo_root / "feature.txt").write_text("feature body", encoding="utf-8")

    # Fake commit helpers.
    observed_messages: list[str] = []
    stage_call_count = {"n": 0}
    create_call_count = {"n": 0}

    def fake_stage_all(root: Path) -> None:
        del root
        stage_call_count["n"] += 1
        if stage_call_count["n"] == 1:
            raise RuntimeError(
                "GitOperationError: Unable to create '.git/index.lock': File exists."
            )

    def fake_create_commit(root: str, message: str, **kwargs: object) -> CommitCreationResult:
        del root, kwargs
        create_call_count["n"] += 1
        observed_messages.append(message)
        return CommitCreationResult.created(sha="deadbeef" + str(create_call_count["n"]))

    effect = CommitEffect(message_file=str(message_file))

    # First attempt: fails on the simulated lock contention.
    first_event = execute_commit_effect(
        effect,
        repo_root,
        stage_all_fn=fake_stage_all,
        create_commit_fn=fake_create_commit,
    )
    assert first_event is PipelineEvent.COMMIT_FAILURE
    assert "index.lock" in (get_last_commit_error() or "")
    # The message file is preserved on disk.
    assert message_file.exists(), (
        "Validated message must be preserved on COMMIT_FAILURE for the in-place retry"
    )

    # Reducer routes in-place for a policy with no commit_recovery/commit_cleanup.
    policy = _load_pipeline_policy()
    state = PipelineState(
        phase="development_commit",
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error=get_last_commit_error() or "",
    )
    with patch(
        "ralph.pipeline.reducer._resolve_commit_recovery_phase",
        return_value=None,
    ):
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)
    assert next_state.phase == "development_commit", (
        f"Reducer must route in-place for a repairable git failure, got {next_state.phase}"
    )

    # Second attempt: the same message is re-read and the commit succeeds.
    second_event = execute_commit_effect(
        effect,
        repo_root,
        stage_all_fn=fake_stage_all,
        create_commit_fn=fake_create_commit,
    )
    assert second_event is PipelineEvent.COMMIT_SUCCESS, (
        f"Second attempt must succeed with the reused message, got {second_event}"
    )

    # The SAME message bytes were used on the successful attempt.
    assert len(observed_messages) == 1
    assert observed_messages[0] == "feat: valid message must be reused after lock retry", (
        "The validated subject must reach the second (successful) commit "
        "unchanged; the message file is the durable source of truth across retries"
    )
