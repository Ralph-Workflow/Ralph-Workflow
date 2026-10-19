"""End-to-end commit-retry real-route tests (Bug 2 AC 4, WU-4E).

The product brief's AC 4 requires that a ``development_commit`` failure
caused by lock contention does not reach ``failed_terminal`` on the
first attempt and the validated commit message is reused on retry.

The existing ``tests/pipeline/test_reducer_lock_retry.py`` covers the
**reducer** seam (in-place re-route, cap clamp, message file survival),
but its ``test_valid_message_preserved_through_failure_then_success``
masks a real-route bug: it calls ``execute_commit_effect`` directly on
the second attempt, bypassing the **real** effect router
(``determine_effect_from_policy``). The router's
``_commit_phase_effect`` (in ``effect_router.py``) gates on
``state.commit.agent_invoked``: when ``False`` (a fresh
``CommitState()``), the router returns ``InvokeAgentEffect`` — the
commit-message agent is re-invoked instead of re-running
``execute_commit_effect`` on the preserved ``effect.message_file``.

The reducer's in-place branch (no recovery phase) called
``progress.advance_phase(advanced_state, target, policy=policy)`` which
resets ``commit = CommitState()`` for ``role == "commit"`` target
phases (``progress.py:190-193``). The fix preserves ``state.commit``
across the in-place retry by ``.copy_with(commit=state.commit, ...)``
in the reducer, so the next effect is ``CommitEffect`` (re-reading
the preserved message file) rather than ``InvokeAgentEffect``.

The default-policy route (``development_commit_cleanup`` per
``ralph/policy/defaults/pipeline.toml:184-195``) is a **separate
concern** out of scope for this fix: the cleanup→commit transition
goes through ``_advance_phase`` →
``progress.advance_phase`` which resets ``commit = CommitState()``
(because ``role == "commit"``), so the commit-message agent is
re-invoked after cleanup. This test documents the current behavior
and surfaces it as a known follow-up.

Each test in this module:

- Places the commit-message artifact inside the actual
  ``WorkspaceScope.root`` so the routed effect (``workspace_scope.root
  / COMMIT_MESSAGE_ARTIFACT``) reads the same file the test wrote.
- Uses fakes for filesystem, git ops, and clock — no real
  ``subprocess``, no ``time.sleep``, no type-suppression comments.
- Drives the real effect router via
  ``determine_effect_from_policy`` and the real reducer
  via ``reduce`` (with the recovery phase patched to ``None`` for the
  in-place branch).
- Executes the ``next_effect`` returned by the router on retry (NOT
  the original ``effect``), so the test exercises the real route.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.pipeline.commit_executor import (
    execute_commit_effect,
    set_last_commit_error,
)
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
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


def _make_commit_message_file(repo_root: Path, subject: str) -> Path:
    """Place the commit-message artifact inside ``repo_root`` (the
    ``WorkspaceScope.root``).

    The effect router builds the routed effect's ``message_file`` as
    ``workspace_scope.root / COMMIT_MESSAGE_ARTIFACT`` (see
    ``ralph/pipeline/effect_router.py:301-302``). The artifact must
    live at that path so the routed retry can re-read it.
    """
    agent_dir = repo_root / ".agent" / "artifacts"
    agent_dir.mkdir(parents=True, exist_ok=True)
    message_file = agent_dir / "commit_message.md"
    message_file.write_text(
        f"---\ntype: commit\nsubject: {subject}\n---\n",
        encoding="utf-8",
    )
    return message_file


def _make_git_repo(tmp_path: Path) -> Path:
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


# ---------------------------------------------------------------------------
# In-place route (no recovery phase). After the WU-4E production fix, the
# next effect is ``CommitEffect`` (re-reading the preserved message file),
# NOT ``InvokeAgentEffect`` (which would re-invoke the commit-message agent).
# ---------------------------------------------------------------------------


def test_in_place_retry_next_effect_is_commit_effect_not_invoke_agent(
    tmp_path: Path,
) -> None:
    """In-place lock-contention retry preserves commit state across the retry.

    Drives the real effect router (``determine_effect_from_policy``) on the
    post-reduce state. After the WU-4E production fix:

    1. The reducer's in-place branch preserves ``state.commit`` so
       ``state.commit.agent_invoked`` remains True across the retry.
    2. ``_commit_phase_effect`` (``effect_router.py:301-311``) sees
       ``agent_invoked=True`` and returns ``CommitEffect`` (NOT
       ``InvokeAgentEffect``) on the second pass.
    3. ``execute_commit_effect`` re-reads the preserved message file and
       lands the commit.

    Before the fix, the in-place branch reset ``commit = CommitState()``
    via ``progress.advance_phase``, so the next effect was
    ``InvokeAgentEffect`` — the commit-message agent was re-invoked and
    the validated message bytes were discarded.

    Crucially, the test exercises the **real effect route**: it places
    the commit-message artifact at ``workspace_scope.root /
    COMMIT_MESSAGE_ARTIFACT``, drives the real effect router on the
    post-reduce state, and executes the returned ``next_effect`` (NOT
    the original ``effect``). A broken reducer fix that preserved
    ``state.commit`` but routed the message file to the wrong path
    would fail this test.
    """
    from ralph.git.commit_result import CommitCreationResult

    repo_root = _make_git_repo(tmp_path)
    message_file = _make_commit_message_file(
        repo_root, "feat: validated message must be reused on in-place retry"
    )
    scope = _make_workspace_scope(repo_root)
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    # CommitState seeded with agent_invoked=True to reflect that the
    # commit-message agent has already run and produced a validated message.
    from ralph.pipeline.commit_state import CommitState

    commit_state = CommitState(message_prepared=True, diff_prepared=True, agent_invoked=True)

    # The action callback raises a lock error on the first call, then
    # succeeds (mirrors the real-world recovery: a stale lock was
    # unlinked, so the second ``git add -A`` succeeds).
    stage_call_count = {"n": 0}
    observed_messages: list[str] = []

    def fake_stage_all(root: Path) -> None:
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

    # The first attempt's effect points to the canonical message-file
    # path inside ``repo_root`` (the same path the router will build).
    effect = CommitEffect(message_file=str(message_file))

    # First attempt: lock-contention failure.
    first_event = execute_commit_effect(
        effect,
        repo_root,
        stage_all_fn=fake_stage_all,
        create_commit_fn=fake_create_commit,
    )
    assert first_event is PipelineEvent.COMMIT_FAILURE
    assert message_file.exists(), "Validated message must be preserved on COMMIT_FAILURE"

    # Reducer routes in-place for a policy with no commit_recovery/commit_cleanup
    # phase. We patch _resolve_commit_recovery_phase to None to force the
    # in-place branch (the default policy DOES define a commit_cleanup phase;
    # this test focuses on the in-place branch behavior).
    state = PipelineState(
        phase="development_commit",
        phase_chains={
            "development_commit": AgentChainState(agents=["claude"], current_index=0, retries=0)
        },
        commit=commit_state,
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
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

    # The fix: state.commit is preserved across the in-place retry, so
    # agent_invoked remains True and the next effect is CommitEffect.
    assert next_state.commit.agent_invoked is True, (
        "state.commit.agent_invoked must be preserved across the in-place "
        "retry; otherwise the next effect falls through to InvokeAgentEffect "
        "and the validated message is discarded"
    )

    # Now drive the real effect router on the post-reduce state. The next
    # effect must be CommitEffect, NOT InvokeAgentEffect.
    next_effect = determine_effect_from_policy(next_state, policy_bundle, workspace_scope=scope)
    assert isinstance(next_effect, CommitEffect), (
        f"In-place lock-retry next effect must be CommitEffect "
        f"(re-running execute_commit_effect on the preserved message "
        f"file), got {type(next_effect).__name__}. If this is an "
        f"InvokeAgentEffect, the commit-message agent is being re-invoked "
        f"and the validated message is being discarded."
    )
    assert not isinstance(next_effect, InvokeAgentEffect), (
        "InvokeAgentEffect would mean the commit-message agent is re-invoked; "
        "the validated message bytes from the first pass must be reused, "
        "not regenerated by re-running the agent."
    )

    # The routed effect must read the SAME message file the test wrote
    # (i.e. the artifact is inside the WorkspaceScope.root).
    expected_message_file = scope.root / ".agent" / "artifacts" / "commit_message.md"
    assert Path(next_effect.message_file) == expected_message_file, (
        f"next_effect.message_file must point at the canonical artifact path "
        f"({expected_message_file}); got {next_effect.message_file}"
    )

    # Second attempt: the REAL execute_commit_effect, driven by the
    # ``next_effect`` returned by the router, re-reads the preserved
    # message and lands the commit. The validated subject reaches the
    # second (successful) commit unchanged.
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
    assert observed_messages[0] == "feat: validated message must be reused on in-place retry"


# ---------------------------------------------------------------------------
# Default-policy ``commit_cleanup`` route. Documented current behavior:
# the cleanup→commit transition re-invokes the commit-message agent
# because ``progress.advance_phase`` resets ``commit = CommitState()`` on
# the cleanup→commit boundary. This is a known follow-up out of scope for
# WU-4E.
# ---------------------------------------------------------------------------


def test_default_policy_routes_to_commit_cleanup_phase(tmp_path: Path) -> None:
    """Default-policy lock-contention routes to ``development_commit_cleanup``.

    Documents the default-policy behavior: a ``development_commit`` lock
    failure on the default pipeline (which defines
    ``development_commit_cleanup`` per
    ``ralph/policy/defaults/pipeline.toml:184-195``) routes to
    ``development_commit_cleanup`` for the cleanup agent, NOT to
    ``failed_terminal`` on the first attempt.
    """
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    # Sanity: the default policy does define a development_commit_cleanup
    # phase (this is what the WU-4E fix does NOT change).
    assert "development_commit_cleanup" in pipeline_policy.phases, (
        "Default policy must define development_commit_cleanup"
    )

    state = PipelineState(
        phase="development_commit",
        phase_chains={
            "development_commit": AgentChainState(agents=["claude"], current_index=0, retries=0)
        },
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
    )
    set_last_commit_error(state.last_error or "")
    try:
        # No patch: the default policy's commit_cleanup phase is honored.
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
    finally:
        set_last_commit_error(None)

    # First-attempt lock-contention does NOT reach failed_terminal.
    assert next_state.phase != "failed_terminal", (
        "Default-policy lock-contention must not reach failed_terminal "
        "on the first attempt; the cleanup phase must run first."
    )
    # Reducer routes to the cleanup phase.
    assert next_state.phase == "development_commit_cleanup", (
        f"Default-policy lock-contention must route to "
        f"development_commit_cleanup, got phase={next_state.phase}"
    )
    # pending_cycle_outcome cleared (commit did not succeed).
    assert next_state.pending_cycle_outcome is None


def test_default_policy_cleanup_to_commit_re_invokes_commit_message_agent(
    tmp_path: Path,
) -> None:
    """Default-policy cleanup→commit transition re-invokes the commit-message agent.

    Documents the **current** behavior of the default-policy route
    through the **real** effect router. After
    ``reduce(state, PipelineEvent.COMMIT_FAILURE, ...)`` routes the
    failure to ``development_commit_cleanup`` and a subsequent
    ``PipelineEvent.PHASE_ADVANCE`` advances cleanup to
    ``development_commit``, the
    :func:`progress.advance_phase` helper resets
    ``commit = CommitState()`` (because ``role == "commit"`` per
    ``progress.py:190-193``). The routed next effect is therefore
    ``InvokeAgentEffect`` — the commit-message agent is re-invoked.

    This is the documented current behavior, NOT a defect of this fix.
    The WU-4E in-place repair preserves ``state.commit`` ONLY in the
    in-place branch (no recovery phase); the cleanup→commit transition
    goes through ``_resolve_or_terminal`` →
    ``progress.advance_phase`` which is out of scope for the in-place
    repair. The commit-message agent being re-invoked on the
    cleanup→commit transition is a known follow-up surfaced here so
    it can be addressed as a separate, larger change.
    """
    # A real git repo is required so the router's
    # ``has_uncommitted_changes`` check on the workspace root does not
    # raise (otherwise it returns ``ExitFailureEffect`` before reaching
    # the ``_commit_phase_effect`` branch).
    repo_root = _make_git_repo(tmp_path)
    scope = _make_workspace_scope(repo_root)
    policy_bundle = _load_policy_bundle()
    pipeline_policy = policy_bundle.pipeline

    state = PipelineState(
        phase="development_commit",
        phase_chains={
            "development_commit": AgentChainState(agents=["claude"], current_index=0, retries=0)
        },
        recovery_epoch=0,
        pending_cycle_outcome="completed",
        last_error="GitOperationError: Unable to create '.git/index.lock': File exists",
    )
    set_last_commit_error(state.last_error or "")
    try:
        # Step 1: lock-contention routes to development_commit_cleanup.
        cleanup_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, pipeline_policy)
    finally:
        set_last_commit_error(None)

    assert cleanup_state.phase == "development_commit_cleanup", (
        f"Default-policy lock-contention must route to "
        f"development_commit_cleanup, got phase={cleanup_state.phase}"
    )

    # Step 2: simulate cleanup-phase completion by sending
    # PHASE_ADVANCE. The default-policy cleanup phase transitions to
    # ``development_commit`` on success.
    advanced_state, _ = reduce(cleanup_state, PipelineEvent.PHASE_ADVANCE, pipeline_policy)
    assert advanced_state.phase == "development_commit", (
        f"Cleanup→commit transition must land on development_commit, "
        f"got phase={advanced_state.phase}"
    )

    # Step 3: drive the real effect router on the post-cleanup state.
    # The router reads ``commit.agent_invoked`` from the post-cleanup
    # state, which has been reset to a fresh ``CommitState()`` by
    # ``progress.advance_phase`` (because ``role == "commit"``). The
    # next effect is therefore ``InvokeAgentEffect`` — the
    # commit-message agent is re-invoked. This is the documented current
    # behavior; the follow-up fix (cleanup→commit commit-state
    # preservation) is out of scope for WU-4E.
    next_effect = determine_effect_from_policy(advanced_state, policy_bundle, workspace_scope=scope)

    # The next effect after cleanup→commit is InvokeAgentEffect
    # (current behavior); the validated message is NOT reused.
    assert isinstance(next_effect, InvokeAgentEffect), (
        f"Cleanup→commit transition must currently re-invoke the "
        f"commit-message agent (InvokeAgentEffect) because "
        f"progress.advance_phase resets commit on entering "
        f"role=='commit' phases; got {type(next_effect).__name__}. "
        f"This is the documented current behavior; the follow-up fix "
        f"is out of scope for WU-4E."
    )
    assert not isinstance(next_effect, CommitEffect), (
        "CommitEffect would mean the validated message bytes are "
        "reused on the cleanup→commit transition; this is the WU-4E "
        "follow-up behavior, not the current one."
    )
