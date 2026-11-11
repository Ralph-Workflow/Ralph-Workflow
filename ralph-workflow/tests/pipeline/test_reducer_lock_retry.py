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
    from ralph.policy.models import PipelinePolicy, PolicyBundle

pytestmark = pytest.mark.timeout_seconds(5)


def _load_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _load_pipeline_policy() -> PipelinePolicy:
    return _load_policy_bundle().pipeline


def test_no_recovery_phase_routes_in_place() -> None:
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

        assert next_state.phase != "failed_terminal"
        assert next_state.phase == "development_commit"
        assert next_state.pending_cycle_outcome is None
        assert next_state.recovery_epoch == 1
    finally:
        set_last_commit_error(None)


def test_recovery_epoch_clamps_at_max() -> None:
    policy = _load_pipeline_policy()
    cap = _MAX_COMMIT_RECOVERY_ATTEMPTS

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
        assert next_state.recovery_epoch == cap, (
            f"recovery_epoch must be clamped at the cap, got {next_state.recovery_epoch}"
        )
        assert next_state.pending_cycle_outcome is None

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
    from ralph.pipeline.commit_executor import execute_commit_effect

    agent_dir = tmp_path / ".agent" / "artifacts"
    agent_dir.mkdir(parents=True, exist_ok=True)
    message_file = agent_dir / "commit_message.md"
    message_file.write_text(
        "---\ntype: commit\nsubject: feat: implement lock-retry test\n---\n",
        encoding="utf-8",
    )

    assert message_file.exists(), "Message file must exist before failure handling"

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

        assert next_state.phase == "development_commit"
        assert next_state.pending_cycle_outcome is None

        assert message_file.exists(), (
            "Message file at effect.message_file must survive COMMIT_FAILURE; "
            "the commit executor only deletes it on SUCCESS or SKIPPED"
        )

        effect = CommitEffect(message_file=str(message_file))
        _event = execute_commit_effect(effect, tmp_path)

        assert message_file.exists(), "Message file must still exist after reducer routes in-place"
    finally:
        set_last_commit_error(None)


def _setup_git_worktree_for_retry(tmp_path: Path) -> Path:
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
    (repo_root / "feature.txt").write_text("feature body", encoding="utf-8")
    return repo_root


def test_valid_message_preserved_through_failure_then_success(tmp_path: Path) -> None:
    from ralph.git.commit_result import CommitCreationResult
    from ralph.mcp.artifacts.commit_message import read_commit_message_from_path
    from ralph.pipeline.commit_executor import (
        execute_commit_effect,
        get_last_commit_error,
    )
    from ralph.pipeline.commit_state import CommitState
    from ralph.pipeline.effect_router import determine_effect_from_policy
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.workspace import WorkspaceScope

    repo_root = _setup_git_worktree_for_retry(tmp_path)
    agent_dir = repo_root / ".agent" / "artifacts"
    agent_dir.mkdir(parents=True, exist_ok=True)
    message_file = agent_dir / "commit_message.md"
    message_text = (
        "---\n"
        "type: commit\n"
        "subject: feat: valid message must be reused after lock retry\n"
        "---\n\n"
        "## Body Summary\n\n"
        "- [BS-1] Multiline summary of commit for lock retry test.\n\n"
        "## Body Details\n\n"
        "- [BD-1] Technical detail explaining byte-identical message reuse.\n"
    )
    original_bytes = message_text.encode("utf-8")
    message_file.write_bytes(original_bytes)

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

    first_event = execute_commit_effect(
        effect,
        repo_root,
        stage_all_fn=fake_stage_all,
        create_commit_fn=fake_create_commit,
    )
    assert first_event is PipelineEvent.COMMIT_FAILURE
    assert "index.lock" in (get_last_commit_error() or "")
    assert message_file.exists(), (
        "Validated message must be preserved on COMMIT_FAILURE for the in-place retry"
    )
    assert message_file.read_bytes() == original_bytes, (
        "Validated message bytes must be preserved byte-for-byte after failure"
    )

    policy_bundle = _load_policy_bundle()
    policy = policy_bundle.pipeline
    state = PipelineState(
        phase="development_commit",
        commit=CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True),
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
    assert next_state.commit.agent_invoked is True, (
        "In-place lock retry must preserve commit.agent_invoked=True"
    )

    scope = WorkspaceScope(
        root=repo_root,
        allowed_roots=(repo_root,),
        local_config_path=repo_root / ".agent" / "ralph-workflow.toml",
        propagated_config_paths=(),
    )
    next_effect = determine_effect_from_policy(
        next_state,
        policy_bundle,
        workspace_scope=scope,
        has_uncommitted_changes_fn=lambda _: True,
    )
    assert not isinstance(next_effect, InvokeAgentEffect), (
        "Router must not re-invoke agent when validated commit message exists"
    )
    assert isinstance(next_effect, CommitEffect), (
        f"Next effect on in-place retry must be CommitEffect, got {type(next_effect).__name__}"
    )
    assert Path(next_effect.message_file) == message_file

    assert message_file.read_bytes() == original_bytes, (
        "Artifact bytes must remain byte-identical before retry attempt"
    )
    expected_rendered_message = read_commit_message_from_path(message_file)
    assert expected_rendered_message is not None
    assert "\n\n" in expected_rendered_message, "Must be a multiline message"

    second_event = execute_commit_effect(
        next_effect,
        repo_root,
        stage_all_fn=fake_stage_all,
        create_commit_fn=fake_create_commit,
    )
    assert second_event is PipelineEvent.COMMIT_SUCCESS, (
        f"Second attempt must succeed with the reused message, got {second_event}"
    )

    assert len(observed_messages) == 1
    assert observed_messages[0] == expected_rendered_message, (
        "The validated multiline message must reach the second (successful) commit "
        "unchanged; the message file is the durable source of truth across retries"
    )


def test_persistent_lock_contention_never_reaches_failed_terminal_and_pauses() -> None:
    from ralph.pipeline.reducer import LOCK_RETRY_DELAY_MS

    policy = _load_pipeline_policy()
    err = "GitOperationError: Git stage_all failed: fatal: Unable to create '/r/.git/index.lock': File exists."
    state = PipelineState(
        phase="development_commit",
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error=err,
    )
    try:
        for _ in range(_MAX_COMMIT_RECOVERY_ATTEMPTS * 4):
            set_last_commit_error(err)
            state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)
            assert state.phase != policy.recovery.failed_route
            assert state.recovery_epoch <= _MAX_COMMIT_RECOVERY_ATTEMPTS
            assert state.last_retry_delay_ms == LOCK_RETRY_DELAY_MS
            state = state.copy_with(last_retry_delay_ms=0, pending_cycle_outcome="completed")
    finally:
        set_last_commit_error(None)
