"""End-to-end pipeline test: development_commit_cleanup -> development_commit.

Black-box proof that the cleanup -> commit transition works through the
real ``runner.run`` harness with real git operations on the filesystem
(NOT a direct call to ``handle_commit_cleanup_phase``).

Mirrors the canonical pattern from
``tests/integration/test_pipeline_with_engine_internal_artifacts.py``:

* Real ``FsWorkspace`` against a real ``tmp_git_repo`` (the cleanup
  phase mutates the real ``.gitignore`` and real ``.git/info/exclude``).
* ``CommitCleanupAlwaysLoopbackInvoker`` helper to drive every cleanup
  run to loopback via its overridden ``commit_event_for``.
* Patches ``phase_event_after_agent_run`` so cleanup phases actually
  execute ``handle_commit_cleanup_phase`` (the helper's
  ``commit_event_for`` would otherwise short-circuit the cleanup work
  with a ``PHASE_LOOPBACK`` stub); non-cleanup phases still go through
  the standard mock seam so the pipeline can complete.
* Observes the real git log via ``Repo.head.log`` after the pipeline
  terminates -- proves the cleanup -> commit transition actually
  created a commit.

This test is the strongest possible proof that the original user-reported
failure mode (``Refusing to delete non-housekeeping file: ...``) cannot
recur: the pipeline terminates at ``complete`` (NOT ``failed_terminal``),
every originally-failing tracked file is deleted from disk, AND the
canonical ``.gitignore`` / ``.git/info/exclude`` patterns were
auto-seeded. Per-test timeout is capped at 15s (well under the 60s
combined budget enforced by ``ralph/verify.py``).

Two e2e tests live here:

1. ``test_pipeline_cleanup_to_commit_end_to_end`` -- the canonical
   proof. Pre-stages the three originally-failing tracked paths AND a
   SEPARATE innocent symlink (PA-005 distinct paths). Submits a
   cleanup artifact deleting the three originally-failing paths but
   NOT the symlink. Asserts the pipeline reaches ``complete``,
   observes ``Repo.head.log`` directly (NOT ``repo.iter_commits``)
   to prove a new commit was actually created, and asserts the
   untouched innocent symlink is preserved.

2. ``test_pipeline_cleanup_to_commit_rejects_symlink_delete_end_to_end``
   -- the PA-005 BLACK-BOX proof that the ``delete_file_from_repo``
   hardening is actually exercised by the full pipeline. Submits a
   cleanup artifact that ALSO actively targets the innocent symlink
   and asserts the pipeline surfaces the rejection (the symlink
   survives both as a symlink AND as a target), the originally-
   failing tracked files are STILL cleaned up in the same run, and
   the pipeline still terminates at ``complete`` (the best-effort
   ``_apply_safe_deletes`` try/except absorbs the rejection as a
   WARNING). This is the active-symlink-delete proof that the
   ``test_pipeline_cleanup_to_commit_end_to_end`` test does not
   cover (which only verifies passive preservation of an
   untouched symlink).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from git import Repo
from rich.console import Console

from ralph.agents.chain import ChainManager
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.operations import create_commit, stage_all
from ralph.phases import PhaseContext, handle_phase
from ralph.pipeline import runner
from ralph.pipeline.effects import (
    CommitEffect,
    EarlySkipCommitEffect,
    Effect,
    InvokeAgentEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests.integration._commit_cleanup_always_loopback_invoker import (
    CommitCleanupAlwaysLoopbackInvoker,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle
    from ralph.workspace.memory import MemoryWorkspace

DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"

pytestmark = [pytest.mark.timeout_seconds(15), pytest.mark.subprocess_e2e]

ORIGINALLY_FAILING_PATHS: tuple[str, ...] = (
    "checkpoint.json",
    ".agent/raw/opencode.log",
    ".agent/tmp/mcp-server.log",
)

INNOCENT_SYMLINK = "innocent_link"
INNOCENT_SYMLINK_TARGET = "some_real_file.txt"

EXPECTED_GITIGNORE_FRAGMENTS: tuple[str, ...] = (
    ".agent/",
    "/checkpoint.json",
)

EXPECTED_GIT_EXCLUDE_FRAGMENTS: tuple[str, ...] = (
    ".agent/raw/",
    ".agent/tmp/",
    ".agent/completion_seen_*.json",
    "/checkpoint.json",
)


def _write_commit_cleanup_artifact(workspace: FsWorkspace, content: dict) -> None:
    """Write a commit_cleanup artifact to the workspace."""
    artifact = {
        "name": "commit_cleanup",
        "type": "commit_cleanup",
        "content": content,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    path = Path(workspace.root) / ".agent" / "artifacts" / "commit_cleanup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")


def _write_commit_message_artifacts(repo_root: Path, subject: str) -> None:
    """Write a valid commit_message artifact pair so execute_commit_effect can run.

    The canonical ``execute_commit_effect`` reads ``.agent/tmp/commit_message.json``
    (structured payload) AND ``.agent/tmp/commit-message.txt`` (rendered mirror).
    Both must be present and valid. Without these, the real commit effect returns
    COMMIT_FAILURE because the message file is empty / unreadable.
    """
    artifact_path = repo_root / ".agent" / "tmp" / "commit_message.json"
    text_path = repo_root / ".agent" / "tmp" / "commit-message.txt"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_payload = {
        "name": "commit_message",
        "type": "commit_message",
        "content": {"type": "commit", "subject": subject},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "metadata": {},
    }
    artifact_path.write_text(json.dumps(artifact_payload), encoding="utf-8")
    text_path.write_text(subject, encoding="utf-8")


def _track_and_commit(repo_root: Path, rel_path: str) -> None:
    """Stage a relative path in ``repo_root`` and commit it."""
    repo = Repo(repo_root)
    try:
        repo.index.add([rel_path])
        repo.index.commit(f"track {rel_path}")
    finally:
        repo.close()


@pytest.fixture
def engine_internal_workspace_with_symlink(tmp_git_repo: Path) -> FsWorkspace:
    """Pre-stage the originally-failing tracked paths AND an innocent symlink.

    PA-005: the innocent symlink MUST use a distinct path from every
    deletable tracked file so the assertions can simultaneously be
    true (deletable tracked files are gone, symlink is preserved).
    """
    root_checkpoint = tmp_git_repo / "checkpoint.json"
    root_checkpoint.write_text('{"phase": "development"}')
    _track_and_commit(tmp_git_repo, "checkpoint.json")

    raw_log = tmp_git_repo / ".agent" / "raw" / "opencode.log"
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    raw_log.write_text("log content\n")
    _track_and_commit(tmp_git_repo, ".agent/raw/opencode.log")

    tmp_log = tmp_git_repo / ".agent" / "tmp" / "mcp-server.log"
    tmp_log.parent.mkdir(parents=True, exist_ok=True)
    tmp_log.write_text("mcp log\n")
    _track_and_commit(tmp_git_repo, ".agent/tmp/mcp-server.log")

    real_file = tmp_git_repo / INNOCENT_SYMLINK_TARGET
    real_file.write_text("I am the symlink target, not a deletable tracked file\n")
    innocent = tmp_git_repo / INNOCENT_SYMLINK
    innocent.symlink_to(real_file)

    (tmp_git_repo / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)

    return FsWorkspace(tmp_git_repo)


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    return load_policy(DEFAULT_POLICY_DIR)


def _config() -> UnifiedConfig:
    return UnifiedConfig()


def _install_runner_display_context(monkeypatch: MonkeyPatch) -> None:
    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, force_width=120, )
    monkeypatch.setattr(runner, "make_display_context", lambda **_kwargs: ctx)


def _stub_prompt_materialization(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "materialize_prepared_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "materialize_prompt_for_phase", lambda *args, **kwargs: "noop")
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)


def test_pipeline_cleanup_to_commit_end_to_end(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
    engine_internal_workspace_with_symlink: FsWorkspace,
) -> None:
    """End-to-end proof: cleanup -> commit transition creates a real git commit.

    Drives ``runner.run`` end-to-end with the canonical
    ``CommitCleanupAlwaysLoopbackInvoker`` helper PLUS a real
    ``execute_commit_effect`` call for every ``CommitEffect`` (the runner's
    own DI seam). Pre-stages the three originally-failing tracked paths
    AND a SEPARATE innocent symlink (PA-005 distinct paths).

    After the pipeline terminates:
    * Every originally-failing tracked file is deleted from disk.
    * The innocent symlink is preserved (NOT deleted as a target).
    * ``.gitignore`` and ``.git/info/exclude`` were auto-seeded.
    * The pipeline terminated at ``complete`` (NOT ``failed_terminal``).
    * A real git commit was created (Repo.head.log count incremented
      beyond the template's initial commit, and the new commit's tree
      does NOT contain the deleted tracked files).
    """
    repo_root = Path(engine_internal_workspace_with_symlink.root)
    policy_bundle = _default_policy_bundle()

    initial_reflog_shas = _head_log_unique_shas(repo_root)

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert (repo_root / rel_path).exists(), (
            f"Pre-condition failed: {rel_path!r} must exist before cleanup runs"
        )

    _write_commit_cleanup_artifact(
        engine_internal_workspace_with_symlink,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": path} for path in ORIGINALLY_FAILING_PATHS
            ],
        },
    )
    _write_commit_message_artifacts(
        repo_root,
        "fix(commit): harden cleanup -> commit transition end-to-end",
    )

    invoker = CommitCleanupAlwaysLoopbackInvoker(memory_workspace)

    saved_states: list[object] = []

    def fake_execute_effect(
        effect: Effect,
        _config: UnifiedConfig,
        _workspace_scope: WorkspaceScope,
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invoker.invoke(effect.agent_name, effect.phase)
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            return runner.execute_commit_effect(
                effect,
                create_commit,
                stage_all,
                repo_root,
            )
        if isinstance(effect, EarlySkipCommitEffect):
            return PipelineEvent.COMMIT_SKIPPED
        msg = f"Unexpected effect type: {type(effect)!r}"
        raise AssertionError(msg)

    cleanup_phases = {
        "development_commit_cleanup",
        "development_final_commit_cleanup",
    }

    def fake_phase_event_after_agent_run(
        *,
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        policy_bundle: PolicyBundle,
        workspace: FsWorkspace,
        **_kwargs: object,
    ) -> PipelineEvent:
        if effect.phase in cleanup_phases:
            ctx = PhaseContext.model_construct(
                workspace=workspace,
                registry=AgentRegistry.from_config(config),
                chain_manager=ChainManager(policy_bundle.agents),
                pipeline_policy=policy_bundle.pipeline,
                agents_policy=policy_bundle.agents,
                artifacts_policy=policy_bundle.artifacts,
                config=config,
            )
            events = handle_phase(effect, ctx)
            return events[0] if events else PipelineEvent.AGENT_SUCCESS
        if effect.phase in ("development_commit", "development_final_commit"):
            ctx = PhaseContext.model_construct(
                workspace=workspace,
                registry=AgentRegistry.from_config(config),
                chain_manager=ChainManager(policy_bundle.agents),
                pipeline_policy=policy_bundle.pipeline,
                agents_policy=policy_bundle.agents,
                artifacts_policy=policy_bundle.artifacts,
                config=config,
            )
            events = handle_phase(effect, ctx)
            return events[0] if events else PipelineEvent.AGENT_SUCCESS
        commit_event_for = cast(
            "Callable[[str], PipelineEvent] | None",
            getattr(invoker, "commit_event_for", None),
        )
        if commit_event_for is not None:
            return commit_event_for(effect.phase)
        return PipelineEvent.AGENT_SUCCESS

    def capture_saved_state(state: object, *_args: object, **_kwargs: object) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(repo_root))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    _stub_prompt_materialization(monkeypatch)
    monkeypatch.setattr(runner, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(runner, "phase_event_after_agent_run", fake_phase_event_after_agent_run)
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        _config(),
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
    )

    assert result == 0, (
        f"Pipeline did not exit cleanly (rc={result}); the originally-failing "
        "paths must clean up via the runtime-artifact allowlist end-to-end."
    )

    assert invoker.count_for("development_commit_cleanup") >= 1, (
        "CommitCleanupAlwaysLoopbackInvoker must have been invoked for "
        "development_commit_cleanup at least once."
    )

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert not (repo_root / rel_path).exists(), (
            f"{rel_path!r} should have been deleted by the cleanup phase "
            "during the pipeline run"
        )

    innocent_link = repo_root / INNOCENT_SYMLINK
    assert innocent_link.is_symlink(), (
        "The innocent symlink MUST still be a symlink (preserved, not "
        "deleted-as-target and not deleted-as-symlink)."
    )
    assert innocent_link.resolve() == (repo_root / INNOCENT_SYMLINK_TARGET).resolve(), (
        "The innocent symlink MUST still point to its original target."
    )
    assert (repo_root / INNOCENT_SYMLINK_TARGET).exists(), (
        "The symlink's target file MUST NOT be deleted as a side effect."
    )

    gitignore_text = (repo_root / ".gitignore").read_text()
    for fragment in EXPECTED_GITIGNORE_FRAGMENTS:
        assert fragment in gitignore_text, (
            f"Expected {fragment!r} in auto-seeded .gitignore, got:\n{gitignore_text}"
        )

    exclude_text = (repo_root / ".git" / "info" / "exclude").read_text()
    for fragment in EXPECTED_GIT_EXCLUDE_FRAGMENTS:
        assert fragment in exclude_text, (
            f"Expected {fragment!r} in auto-seeded .git/info/exclude, got:\n{exclude_text}"
        )

    final_reflog_shas = _head_log_unique_shas(repo_root)
    new_reflog_shas = final_reflog_shas - initial_reflog_shas
    assert new_reflog_shas, (
        f"Repo.head.log shows NO new commit was created: pre-run SHAs="
        f"{sorted(initial_reflog_shas)}, post-run SHAs={sorted(final_reflog_shas)}. "
        "The execute_commit_effect call must actually stage and commit "
        "changes via real git so the cleanup -> commit transition is "
        "observed by the reflog."
    )

    new_commit_tree_paths = _newest_commit_tree_paths(repo_root)
    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert rel_path not in new_commit_tree_paths, (
            f"Deleted tracked file {rel_path!r} must NOT appear in the new "
            f"commit's tree; got paths: {sorted(new_commit_tree_paths)}"
        )

    final_state = saved_states[-1]
    assert getattr(final_state, "phase", None) == "complete", (
        f"Pipeline must terminate at 'complete', got {getattr(final_state, 'phase', None)!r}. "
        "Entering 'failed_terminal' would mean a PhaseFailureEvent was emitted."
    )


def test_pipeline_cleanup_to_commit_rejects_symlink_delete_end_to_end(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
    engine_internal_workspace_with_symlink: FsWorkspace,
) -> None:
    """End-to-end proof: cleanup -> commit surfaces symlink-delete rejection.

    Regression for the PA-005 gap: prior e2e proof only verified that an
    untouched innocent symlink stayed intact (passive preservation).
    This test actively submits a ``delete_file`` cleanup action
    targeting the distinct-path innocent symlink and asserts the
    end-to-end pipeline surfaces the rejection (the symlink survives
    both as a symlink AND as a target), the originally-failing tracked
    files are STILL cleaned up in the same run, and the pipeline
    terminates at ``complete`` (NOT ``failed_terminal``).

    This is the BLACK-BOX proof that ``delete_file_from_repo``'s
    symlink check (the new hardening wired into the full pipeline
    through ``_apply_safe_deletes``'s try/except Exception which logs
    the rejection at WARNING) is actually exercised by the
    cleanup -> commit transition.
    """
    repo_root = Path(engine_internal_workspace_with_symlink.root)
    policy_bundle = _default_policy_bundle()

    initial_reflog_shas = _head_log_unique_shas(repo_root)

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert (repo_root / rel_path).exists(), (
            f"Pre-condition failed: {rel_path!r} must exist before cleanup runs"
        )
    assert (repo_root / INNOCENT_SYMLINK).is_symlink(), (
        f"Pre-condition failed: {INNOCENT_SYMLINK!r} must be a symlink "
        "before cleanup runs"
    )

    _write_commit_cleanup_artifact(
        engine_internal_workspace_with_symlink,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": path} for path in ORIGINALLY_FAILING_PATHS
            ]
            + [
                {"action": "delete_file", "path": INNOCENT_SYMLINK},
            ],
        },
    )
    _write_commit_message_artifacts(
        repo_root,
        "fix(commit): cleanup rejects symlink deletes end-to-end",
    )

    invoker = CommitCleanupAlwaysLoopbackInvoker(memory_workspace)
    saved_states: list[object] = []

    def fake_execute_effect(
        effect: Effect,
        _config: UnifiedConfig,
        _workspace_scope: WorkspaceScope,
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invoker.invoke(effect.agent_name, effect.phase)
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            return runner.execute_commit_effect(
                effect,
                create_commit,
                stage_all,
                repo_root,
            )
        if isinstance(effect, EarlySkipCommitEffect):
            return PipelineEvent.COMMIT_SKIPPED
        msg = f"Unexpected effect type: {type(effect)!r}"
        raise AssertionError(msg)

    cleanup_phases = {
        "development_commit_cleanup",
        "development_final_commit_cleanup",
    }

    def fake_phase_event_after_agent_run(
        *,
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        policy_bundle: PolicyBundle,
        workspace: FsWorkspace,
        **_kwargs: object,
    ) -> PipelineEvent:
        if effect.phase in cleanup_phases or effect.phase in (
            "development_commit",
            "development_final_commit",
        ):
            ctx = PhaseContext.model_construct(
                workspace=workspace,
                registry=AgentRegistry.from_config(config),
                chain_manager=ChainManager(policy_bundle.agents),
                pipeline_policy=policy_bundle.pipeline,
                agents_policy=policy_bundle.agents,
                artifacts_policy=policy_bundle.artifacts,
                config=config,
            )
            events = handle_phase(effect, ctx)
            return events[0] if events else PipelineEvent.AGENT_SUCCESS
        commit_event_for = cast(
            "Callable[[str], PipelineEvent] | None",
            getattr(invoker, "commit_event_for", None),
        )
        if commit_event_for is not None:
            return commit_event_for(effect.phase)
        return PipelineEvent.AGENT_SUCCESS

    def capture_saved_state(state: object, *_args: object, **_kwargs: object) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(repo_root))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    _stub_prompt_materialization(monkeypatch)
    monkeypatch.setattr(runner, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(runner, "phase_event_after_agent_run", fake_phase_event_after_agent_run)
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        _config(),
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
    )

    assert result == 0, (
        f"Pipeline did not exit cleanly (rc={result}); the symlink delete "
        "rejection must NOT break the cleanup -> commit transition."
    )

    assert invoker.count_for("development_commit_cleanup") >= 1, (
        "CommitCleanupAlwaysLoopbackInvoker must have been invoked for "
        "development_commit_cleanup at least once."
    )

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert not (repo_root / rel_path).exists(), (
            f"{rel_path!r} should have been deleted by the cleanup phase "
            "during the pipeline run"
        )

    innocent_link = repo_root / INNOCENT_SYMLINK
    assert innocent_link.is_symlink(), (
        "Pipeline must surface the symlink-delete rejection: the innocent "
        "symlink MUST still be a symlink (NOT deleted-as-symlink, NOT "
        "deleted-as-target)."
    )
    assert innocent_link.resolve() == (repo_root / INNOCENT_SYMLINK_TARGET).resolve(), (
        "The innocent symlink MUST still point to its original target "
        "after the pipeline rejects the symlink delete attempt."
    )
    assert (repo_root / INNOCENT_SYMLINK_TARGET).exists(), (
        "The symlink's target file MUST NOT be deleted as a side effect "
        "of the symlink-delete rejection."
    )

    final_reflog_shas = _head_log_unique_shas(repo_root)
    new_reflog_shas = final_reflog_shas - initial_reflog_shas
    assert new_reflog_shas, (
        f"Repo.head.log shows NO new commit was created: pre-run SHAs="
        f"{sorted(initial_reflog_shas)}, post-run SHAs={sorted(final_reflog_shas)}. "
        "The execute_commit_effect call must still create a real commit "
        "even when one delete_file action surfaces a symlink rejection."
    )

    new_commit_tree_paths = _newest_commit_tree_paths(repo_root)
    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert rel_path not in new_commit_tree_paths, (
            f"Deleted tracked file {rel_path!r} must NOT appear in the new "
            f"commit's tree; got paths: {sorted(new_commit_tree_paths)}"
        )

    final_state = saved_states[-1]
    assert getattr(final_state, "phase", None) == "complete", (
        f"Pipeline must terminate at 'complete', got {getattr(final_state, 'phase', None)!r}. "
        "A symlink-delete rejection must NOT cause failed_terminal; the "
        "best-effort _apply_safe_deletes try/except absorbs it as a WARNING."
    )


def _newest_commit_tree_paths(repo_root: Path) -> set[str]:
    """Return the set of paths in the most recent commit's tree.

    Used by the e2e test to assert that the originally-failing tracked
    files (which cleanup deleted from disk) are NOT in the new commit's
    tree. The cleanup phase handler uses ``git rm --cached`` so the
    files are staged as deletions; the real ``execute_commit_effect``
    then commits those deletions, so the new commit's tree must not
    contain them.
    """
    repo = Repo(repo_root)
    try:
        newest = repo.head.commit
        paths: set[str] = set()
        for blob in newest.tree.traverse():
            if blob.type == "blob":
                paths.add(blob.path)
        return paths
    finally:
        repo.close()


def _head_log_unique_shas(repo_root: Path) -> set[str]:
    """Return the set of unique SHAs recorded in ``Repo.head.log()``.

    Uses ``Repo.head.log()`` (NOT ``repo.iter_commits('HEAD')``) per
    plan step 17 / AC-07: ``Repo.head.log`` is the reflog-level view
    that captures EVERY change to HEAD (commits, branch switches,
    resets). Deduplicating ``newhexsha`` across the reflog gives the
    set of unique SHAs HEAD has ever pointed to in this session, which
    is the strongest possible black-box signal that the
    cleanup -> commit transition actually created a real commit
    (a new SHA appears in the post-run reflog that was not present in
    the pre-run reflog).

    Returns:
        The set of unique commit SHAs observed in the reflog.
    """
    repo = Repo(repo_root)
    try:
        return {entry.newhexsha for entry in repo.head.log()}
    finally:
        repo.close()
