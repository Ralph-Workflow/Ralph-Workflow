"""End-to-end pipeline test: development_final_commit_cleanup -> development_final_commit.

Black-box proof that the final-commit cleanup -> final-commit transition
works through the real ``runner.run`` harness with real git operations
on the filesystem (NOT a direct call to ``handle_commit_cleanup_phase``).

Mirrors the canonical pattern from
``tests/integration/test_pipeline_with_engine_internal_artifacts.py``:

* Real ``FsWorkspace`` against a real ``tmp_git_repo``.
* Extended ``CommitCleanupAlwaysLoopbackInvoker`` helper that now also
  matches ``development_final_commit_cleanup`` so the final-cleanup
  phase runs through loopback.
* Patches ``phase_event_after_agent_run`` so cleanup phases actually
  execute ``handle_commit_cleanup_phase``.
* Observes pipeline state via the captured state list (no direct git
  log assertion -- the test suite does not exercise the real git
  commit step in e2e mode, only the cleanup -> commit transition
  routing).

This test exercises the ``development_final_commit_cleanup`` handler
which has no other dedicated test in the suite. Per-test timeout is
capped at 15s.

After the pipeline terminates:
* Every originally-failing tracked file is deleted from disk.
* ``.gitignore`` and ``.git/info/exclude`` were auto-seeded.
* The pipeline terminated at ``complete`` (NOT ``failed_terminal``).
* A real git commit was created (observed via ``Repo.head.log`` --
  NOT ``repo.iter_commits`` -- per plan step 18 / AC-08, with a new
  SHA appearing in the post-run reflog that was not present in the
  pre-run reflog, and the new commit's tree does NOT contain the
  deleted tracked files).
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
def engine_internal_workspace(tmp_git_repo: Path) -> FsWorkspace:
    """Pre-stage the originally-failing tracked paths."""
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


def test_pipeline_final_cleanup_to_final_commit_end_to_end(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
    engine_internal_workspace: FsWorkspace,
) -> None:
    """End-to-end proof: final-cleanup -> final-commit transition creates a real git commit.

    Drives ``runner.run`` end-to-end with the canonical
    ``CommitCleanupAlwaysLoopbackInvoker`` helper PLUS a real
    ``execute_commit_effect`` call for every ``CommitEffect`` (the runner's
    own DI seam).

    Exercises the ``development_final_commit_cleanup`` phase through the
    same ``runner.run`` harness as the development variant. The cleanup
    handler is registered for ALL role='commit_cleanup' phases, so the
    same ``handle_commit_cleanup_phase`` handles both the development
    and final-commit cleanup variants.

    After the pipeline terminates:
    * Every originally-failing tracked file is deleted from disk.
    * ``.gitignore`` and ``.git/info/exclude`` were auto-seeded.
    * The pipeline terminated at ``complete`` (NOT ``failed_terminal``).
    * A real git commit was created (Repo.head.log count incremented
      beyond the template's initial commit, and the new commit's tree
      does NOT contain the deleted tracked files).
    """
    repo_root = Path(engine_internal_workspace.root)
    policy_bundle = _default_policy_bundle()

    initial_reflog_shas = _head_log_unique_shas(repo_root)

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert (repo_root / rel_path).exists(), (
            f"Pre-condition failed: {rel_path!r} must exist before cleanup runs"
        )

    _write_commit_cleanup_artifact(
        engine_internal_workspace,
        {
            "analysis_complete": True,
            "actions": [
                {"action": "delete_file", "path": path} for path in ORIGINALLY_FAILING_PATHS
            ],
        },
    )
    _write_commit_message_artifacts(
        repo_root,
        "fix(commit): harden final-cleanup -> final-commit transition",
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

    assert invoker.count_for("development_final_commit_cleanup") >= 1, (
        "CommitCleanupAlwaysLoopbackInvoker must have been invoked for "
        "development_final_commit_cleanup at least once (proves the final-cleanup "
        "phase was actually entered through the pipeline harness)."
    )

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert not (repo_root / rel_path).exists(), (
            f"{rel_path!r} should have been deleted by the cleanup phase "
            "during the pipeline run"
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
        "changes via real git so the final-cleanup -> final-commit "
        "transition is observed by the reflog."
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


def _newest_commit_tree_paths(repo_root: Path) -> set[str]:
    """Return the set of paths in the most recent commit's tree."""
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
    plan step 18 / AC-08: ``Repo.head.log`` is the reflog-level view
    that captures EVERY change to HEAD (commits, branch switches,
    resets). Deduplicating ``newhexsha`` across the reflog gives the
    set of unique SHAs HEAD has ever pointed to in this session, which
    is the strongest possible black-box signal that the
    final-cleanup -> final-commit transition actually created a real
    commit (a new SHA appears in the post-run reflog that was not
    present in the pre-run reflog).

    Returns:
        The set of unique commit SHAs observed in the reflog.
    """
    repo = Repo(repo_root)
    try:
        return {entry.newhexsha for entry in repo.head.log()}
    finally:
        repo.close()
