from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.git.commit_result import CommitCreationResult
from ralph.mcp.artifacts.commit_message import read_commit_message_from_path
from ralph.pipeline.commit_executor import (
    execute_commit_effect,
    set_last_commit_error,
)
from ralph.pipeline.commit_state import CommitState
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy, PolicyBundle

pytestmark = pytest.mark.timeout_seconds(10)


def _load_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _load_pipeline_policy() -> PipelinePolicy:
    return _load_policy_bundle().pipeline


def _make_workspace_scope(repo_root: Path) -> WorkspaceScope:
    return WorkspaceScope(
        root=repo_root,
        allowed_roots=(repo_root,),
        local_config_path=repo_root / ".agent" / "ralph-workflow.toml",
        propagated_config_paths=(),
    )


def _make_unified_config() -> UnifiedConfig:
    return UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))


def _make_fake_workspace(tmp_path: Path) -> Path:
    repo_root = tmp_path / "worktree"
    repo_root.mkdir(parents=True, exist_ok=True)
    return repo_root


def _has_uncommitted_changes(_path: Path) -> bool:
    return True


def _make_commit_message_file(
    repo_root: Path,
    subject: str,
    *,
    body_summary: str | None = "Preserve validated commit message artifact across retry.",
    body_details: str
    | None = "Assert byte-identical multiline message reuse across router transitions.",
) -> Path:
    agent_dir = repo_root / ".agent" / "artifacts"
    agent_dir.mkdir(parents=True, exist_ok=True)
    message_file = agent_dir / "commit_message.md"
    parts = [f"---\ntype: commit\nsubject: {subject}\n---\n"]
    if body_summary is not None:
        parts.append(f"\n## Body Summary\n\n- [BS-1] {body_summary}\n")
    if body_details is not None:
        parts.append(f"\n## Body Details\n\n- [BD-1] {body_details}\n")
    message_file.write_text("".join(parts), encoding="utf-8")
    return message_file


@pytest.fixture(autouse=True)
def _fake_git_ops() -> Iterator[None]:
    with (
        patch("ralph.pipeline.effect_router._is_empty_commit_phase", return_value=False),
        patch("ralph.pipeline.commit_executor._is_git_worktree", return_value=False),
        patch("ralph.pipeline.commit_executor.get_head_sha", return_value="deadbeef"),
    ):
        yield


def test_in_place_retry_next_effect_is_commit_effect_not_invoke_agent(
    tmp_path: Path,
) -> None:
    repo_root = _make_fake_workspace(tmp_path)
    message_file = _make_commit_message_file(
        repo_root, "feat: validated message must be reused on in-place retry"
    )
    original_bytes = message_file.read_bytes()
    expected_message = read_commit_message_from_path(message_file)
    assert expected_message is not None
    assert "\n\n" in expected_message, "Must be a multiline commit message"
    scope = _make_workspace_scope(repo_root)
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    commit_state = CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True)

    stage_call_count = {"n": 0}
    observed_messages: list[str] = []

    def fake_stage_all(root: Path | str) -> None:
        del root
        stage_call_count["n"] += 1
        if stage_call_count["n"] == 1:
            raise RuntimeError(
                "GitOperationError: Unable to create '.git/index.lock': File exists."
            )

    def fake_create_commit(root: str, message: str, **kwargs: object) -> CommitCreationResult:
        del root, kwargs
        observed_messages.append(message)
        return CommitCreationResult.created(sha="cafef00d")

    effect = CommitEffect(message_file=str(message_file))

    with (
        patch("ralph.pipeline.commit_executor._is_git_worktree", return_value=False),
        patch("ralph.pipeline.commit_executor.get_head_sha", return_value="deadbeef"),
    ):
        first_event = execute_commit_effect(
            effect,
            repo_root,
            stage_all_fn=fake_stage_all,
            create_commit_fn=fake_create_commit,
            has_commit_work_fn=_has_uncommitted_changes,
        )
        assert first_event is PipelineEvent.COMMIT_FAILURE
        assert message_file.exists(), "Validated message must be preserved on COMMIT_FAILURE"

        state = PipelineState(
            phase="development_commit",
            phase_chains={
                "development_commit": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
            commit=commit_state,
            recovery_epoch=0,
            pending_cycle_outcome="completed",
            last_error="GitOperationError: worktree not clean: uncommitted changes exist",
        )
        set_last_commit_error(state.last_error or "")
        try:
            with patch(
                "ralph.pipeline.reducer._resolve_commit_recovery_phase",
                return_value=None,
            ):
                next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
        finally:
            set_last_commit_error(None)

        assert next_state.phase == "development_commit", (
            f"Reducer must route in-place for a repairable git failure, got phase={next_state.phase}"
        )
        assert next_state.commit.agent_invoked is True, (
            "state.commit.agent_invoked must be preserved across the in-place "
            "retry; otherwise the next effect falls through to InvokeAgentEffect"
        )

        next_effect = determine_effect_from_policy(
            next_state,
            policy_bundle,
            workspace_scope=scope,
            has_uncommitted_changes_fn=_has_uncommitted_changes,
        )
        assert not isinstance(next_effect, InvokeAgentEffect), (
            "InvokeAgentEffect would mean the commit-message agent is re-invoked; "
            "the validated message bytes from the first pass must be reused."
        )
        assert isinstance(next_effect, CommitEffect), (
            f"In-place lock-retry next effect must be CommitEffect, got {type(next_effect).__name__}."
        )

        expected_message_file = scope.root / ".agent" / "artifacts" / "commit_message.md"
        assert Path(next_effect.message_file) == expected_message_file, (
            f"next_effect.message_file must point at the canonical artifact path "
            f"({expected_message_file}); got {next_effect.message_file}"
        )

        assert message_file.read_bytes() == original_bytes, (
            "Commit message artifact bytes must be preserved byte-for-byte across lock-retry"
        )

        second_event = execute_commit_effect(
            next_effect,
            repo_root,
            stage_all_fn=fake_stage_all,
            create_commit_fn=fake_create_commit,
            has_commit_work_fn=_has_uncommitted_changes,
        )
        assert second_event is PipelineEvent.COMMIT_SUCCESS, (
            f"Second attempt must succeed with the reused message, got {second_event}"
        )
        assert len(observed_messages) == 1
        assert observed_messages[0] == expected_message


def test_default_policy_routes_to_commit_review_phase(tmp_path: Path) -> None:
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    assert "development_commit_review" in pipeline_policy.phases, (
        "Default policy must define development_commit_review"
    )

    state = PipelineState(
        phase="development_commit",
        phase_chains={
            "development_commit": AgentChainState(agents=["claude"], current_index=0, retries=0)
        },
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: worktree not clean: uncommitted changes exist",
    )
    set_last_commit_error(state.last_error or "")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
    finally:
        set_last_commit_error(None)

    assert next_state.phase != "failed_terminal", (
        "Default-policy lock-contention must not reach failed_terminal "
        "on the first attempt; the cleanup phase must run first."
    )
    assert next_state.phase == "development_commit_review", (
        f"Default-policy lock-contention must route to "
        f"development_commit_review, got phase={next_state.phase}"
    )
    assert next_state.pending_cycle_outcome is None


def test_default_policy_cleanup_to_commit_preserves_commit_message_and_emits_commit_effect(
    tmp_path: Path,
) -> None:
    repo_root = _make_fake_workspace(tmp_path)
    expected_subject = "feat: default policy validated message must be reused on retry"
    message_file = _make_commit_message_file(
        repo_root,
        expected_subject,
        body_summary="Default policy cleanup recovery preserves multiline message.",
        body_details="Drive lock contention -> cleanup effect -> AGENT_SUCCESS -> CommitEffect.",
    )
    original_bytes = message_file.read_bytes()
    expected_message = read_commit_message_from_path(message_file)
    assert expected_message is not None
    assert "\n\n" in expected_message, "Must be a multiline message"

    scope = _make_workspace_scope(repo_root)
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    commit_state = CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True)
    observed_messages: list[str] = []

    def fake_stage_all(root: Path | str) -> None:
        del root

    def fake_create_commit(root: str, message: str, **kwargs: object) -> CommitCreationResult:
        del root, kwargs
        observed_messages.append(message)
        return CommitCreationResult.created(sha="feedbeef")

    state = PipelineState(
        phase="development_commit",
        phase_chains={
            "development_commit": AgentChainState(agents=["claude"], current_index=0, retries=0),
            "development_commit_review": AgentChainState(
                agents=["claude"], current_index=0, retries=0
            ),
        },
        commit=commit_state,
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: worktree not clean: uncommitted changes exist",
    )
    set_last_commit_error(state.last_error or "")
    try:
        cleanup_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
    finally:
        set_last_commit_error(None)

    assert cleanup_state.phase == "development_commit_review", (
        f"Default-policy lock-contention must route to "
        f"development_commit_review, got phase={cleanup_state.phase}"
    )
    assert cleanup_state.recovery_epoch == 1
    assert cleanup_state.commit.agent_invoked is True

    cleanup_effect = determine_effect_from_policy(
        cleanup_state,
        policy_bundle,
        workspace_scope=scope,
        has_uncommitted_changes_fn=_has_uncommitted_changes,
    )
    assert isinstance(cleanup_effect, InvokeAgentEffect), (
        f"Cleanup phase must emit InvokeAgentEffect, got {type(cleanup_effect).__name__}"
    )
    assert cleanup_effect.phase == "development_commit_review", (
        f"Cleanup effect must target development_commit_review, got {cleanup_effect.phase}"
    )

    assert message_file.read_bytes() == original_bytes, (
        "Commit message artifact bytes must remain intact during cleanup phase"
    )

    advanced_state, _ = reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, pipeline_policy)
    assert advanced_state.phase == "development_commit", (
        f"Cleanup AGENT_SUCCESS must advance to development_commit, "
        f"got phase={advanced_state.phase}"
    )
    assert advanced_state.commit.agent_invoked is True, (
        "state.commit.agent_invoked must be preserved across cleanup→commit transition"
    )
    assert advanced_state.recovery_epoch == 1

    next_effect = determine_effect_from_policy(
        advanced_state,
        policy_bundle,
        workspace_scope=scope,
        has_uncommitted_changes_fn=_has_uncommitted_changes,
    )
    assert not isinstance(next_effect, InvokeAgentEffect), (
        "InvokeAgentEffect must NOT be emitted; commit-message agent must not be re-invoked."
    )
    assert isinstance(next_effect, CommitEffect), (
        f"Cleanup→commit next effect must be CommitEffect (reusing validated message), "
        f"got {type(next_effect).__name__}."
    )
    assert Path(next_effect.message_file) == message_file

    assert message_file.read_bytes() == original_bytes, (
        "Commit message artifact bytes must remain intact before retry commit execution"
    )

    with (
        patch("ralph.pipeline.commit_executor._is_git_worktree", return_value=False),
        patch("ralph.pipeline.commit_executor.get_head_sha", return_value="deadbeef"),
    ):
        event = execute_commit_effect(
            next_effect,
            repo_root,
            stage_all_fn=fake_stage_all,
            create_commit_fn=fake_create_commit,
            has_commit_work_fn=_has_uncommitted_changes,
        )
    assert event is PipelineEvent.COMMIT_SUCCESS
    assert observed_messages == [expected_message]


def test_fresh_commit_entry_resets_commit_state_and_invokes_agent(tmp_path: Path) -> None:
    repo_root = _make_fake_workspace(tmp_path)
    scope = _make_workspace_scope(repo_root)
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    stale_commit = CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True)
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
        },
        commit=stale_commit,
        recovery_epoch=0,
    )

    cleanup_state, _ = reduce(state, PipelineEvent.PHASE_ADVANCE, pipeline_policy)
    assert cleanup_state.phase == "development_commit_review"
    assert cleanup_state.recovery_epoch == 0
    assert cleanup_state.last_error is None

    commit_state, _ = reduce(cleanup_state, PipelineEvent.PHASE_ADVANCE, pipeline_policy)
    assert commit_state.phase == "development_commit"
    assert commit_state.commit.agent_invoked is False, (
        "Fresh entry into commit phase must reset CommitState (agent_invoked=False)"
    )
    assert commit_state.recovery_epoch == 0

    next_effect = determine_effect_from_policy(
        commit_state,
        policy_bundle,
        workspace_scope=scope,
        has_uncommitted_changes_fn=_has_uncommitted_changes,
    )
    assert not isinstance(next_effect, CommitEffect)
    assert isinstance(next_effect, InvokeAgentEffect), (
        f"Fresh commit entry must emit InvokeAgentEffect, got {type(next_effect).__name__}"
    )


def test_cleanup_failure_does_not_reuse_commit_message(tmp_path: Path) -> None:
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    cleanup_state = PipelineState(
        phase="development_commit_review",
        previous_phase="development_commit",
        phase_chains={
            "development_commit_review": AgentChainState(
                agents=["claude"], current_index=0, retries=0
            )
        },
        commit=CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True),
        recovery_epoch=1,
        last_error="GitOperationError: worktree not clean: uncommitted changes exist",
    )

    failure_event = PhaseFailureEvent(
        phase="development_commit_review",
        reason="cleanup script failed unrecoverably",
        recoverable=False,
    )
    next_state, _ = reduce(cleanup_state, failure_event, pipeline_policy)

    assert next_state.phase == pipeline_policy.recovery.failed_route, (
        f"Unrecoverable cleanup failure must route to failed_terminal, got {next_state.phase}"
    )
    assert next_state.phase != "development_commit"


def test_cleanup_without_validated_message_does_not_emit_commit_effect(
    tmp_path: Path,
) -> None:
    repo_root = _make_fake_workspace(tmp_path)
    scope = _make_workspace_scope(repo_root)
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    cleanup_state = PipelineState(
        phase="development_commit_review",
        previous_phase="development_commit",
        phase_chains={
            "development_commit_review": AgentChainState(
                agents=["claude"], current_index=0, retries=0
            )
        },
        commit=CommitState(message_prepared=False, diff_prepared=False, agent_invoked=False),
        recovery_epoch=1,
        last_error="GitOperationError: worktree not clean: uncommitted changes exist",
    )

    advanced_state, _ = reduce(cleanup_state, PipelineEvent.PHASE_ADVANCE, pipeline_policy)
    assert advanced_state.phase == "development_commit"
    assert advanced_state.commit.agent_invoked is False

    next_effect = determine_effect_from_policy(
        advanced_state,
        policy_bundle,
        workspace_scope=scope,
        has_uncommitted_changes_fn=_has_uncommitted_changes,
    )
    assert not isinstance(next_effect, CommitEffect)
    assert isinstance(next_effect, InvokeAgentEffect)


def test_malformed_artifact_on_commit_retry_regenerates_the_message(
    tmp_path: Path,
) -> None:
    repo_root = _make_fake_workspace(tmp_path)
    agent_dir = repo_root / ".agent" / "artifacts"
    agent_dir.mkdir(parents=True, exist_ok=True)
    corrupt_message_file = agent_dir / "commit_message.md"
    corrupt_message_file.write_text("invalid artifact content without yaml", encoding="utf-8")

    scope = _make_workspace_scope(repo_root)
    policy_bundle = _load_policy_bundle()
    commit_state = CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True)
    state = PipelineState(
        phase="development_commit",
        commit=commit_state,
        recovery_epoch=1,
        last_error="GitOperationError: worktree not clean: uncommitted changes exist",
    )

    next_effect = determine_effect_from_policy(
        state,
        policy_bundle,
        workspace_scope=scope,
        has_uncommitted_changes_fn=_has_uncommitted_changes,
    )
    assert isinstance(next_effect, InvokeAgentEffect)
    assert next_effect.phase == "development_commit"


def test_repeated_lock_contention_preserves_and_advances_recovery_epoch(
    tmp_path: Path,
) -> None:
    repo_root = _make_fake_workspace(tmp_path)
    _make_commit_message_file(repo_root, "feat: repeated lock contention test")
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    commit_state = CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True)
    repairable_error = "GitOperationError: worktree not clean: uncommitted changes exist"

    state = PipelineState(
        phase="development_commit",
        commit=commit_state,
        recovery_epoch=0,
        last_error=repairable_error,
    )

    set_last_commit_error(repairable_error)
    cleanup_1, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
    assert cleanup_1.phase == "development_commit_review"
    assert cleanup_1.recovery_epoch == 1
    assert cleanup_1.commit.agent_invoked is True

    commit_1, _ = reduce(cleanup_1, PipelineEvent.PHASE_ADVANCE, pipeline_policy)
    assert commit_1.phase == "development_commit"
    assert commit_1.recovery_epoch == 1
    assert commit_1.commit.agent_invoked is True

    cleanup_2, _ = reduce(commit_1, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
    assert cleanup_2.phase == "development_commit_review"
    assert cleanup_2.recovery_epoch == 2

    commit_2, _ = reduce(cleanup_2, PipelineEvent.PHASE_ADVANCE, pipeline_policy)
    assert commit_2.phase == "development_commit"
    assert commit_2.recovery_epoch == 2
    cleanup_3, _ = reduce(commit_2, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
    assert cleanup_3.phase == "development_commit_review"
    assert cleanup_3.recovery_epoch == 3

    commit_3, _ = reduce(cleanup_3, PipelineEvent.PHASE_ADVANCE, pipeline_policy)
    assert commit_3.phase == "development_commit"
    assert commit_3.recovery_epoch == 3

    cleanup_4, _ = reduce(commit_3, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
    assert cleanup_4.phase == pipeline_policy.recovery.failed_route
    assert cleanup_4.recovery_epoch == 3
    assert cleanup_4.last_execution_result_status == "failed"

    commit_success_state, _ = reduce(commit_3, PipelineEvent.COMMIT_SUCCESS, pipeline_policy)
    assert commit_success_state.phase == "development_analysis"
    assert commit_success_state.recovery_epoch == 0


def test_stale_recovery_epoch_does_not_reuse_an_old_commit_invocation() -> None:
    policy = _load_policy_bundle().pipeline
    stale_state = PipelineState(
        phase="development_commit_review",
        commit=CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True),
        recovery_epoch=1,
        last_error=None,
    )

    commit_state, _ = reduce(stale_state, PipelineEvent.PHASE_ADVANCE, policy)

    assert commit_state.phase == "development_commit"
    assert commit_state.commit.agent_invoked is False
