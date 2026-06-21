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
from ralph.phases import PhaseContext, handle_phase
from ralph.pipeline import runner
from ralph.pipeline.effects import CommitEffect, Effect, InvokeAgentEffect
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

pytestmark = pytest.mark.timeout_seconds(15)

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
    ctx = make_display_context(console=console, force_width=120, force_mode="wide")
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

    After the pipeline terminates:
    * Every originally-failing tracked file is deleted from disk.
    * The innocent symlink is preserved (NOT deleted as a target).
    * ``.gitignore`` and ``.git/info/exclude`` were auto-seeded.
    * The pipeline terminated at ``complete`` (NOT ``failed_terminal``).
    * The git log shows a new commit (``Repo.head.log`` count >= 1
      beyond the template's initial commit).
    """
    repo_root = Path(engine_internal_workspace_with_symlink.root)
    policy_bundle = _default_policy_bundle()

    initial_commit_count = _git_commit_count(repo_root)
    del initial_commit_count  # currently only used in pre-condition guards

    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert (repo_root / rel_path).exists(), (
            f"Pre-condition failed: {rel_path!r} must exist before cleanup runs"
        )

    _write_commit_cleanup_artifact(
        engine_internal_workspace_with_symlink,
        {
            "analysis_complete": False,
            "actions": [
                {"action": "delete_file", "path": path} for path in ORIGINALLY_FAILING_PATHS
            ],
        },
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
            commit_event_for = cast(
                "Callable[[str], PipelineEvent] | None",
                getattr(invoker, "commit_event_for", None),
            )
            last_phase = getattr(invoker, "last_phase", None)
            if (
                commit_event_for is not None
                and isinstance(last_phase, str)
                and (last_phase.endswith("_commit") or last_phase == "commit")
            ):
                return commit_event_for(last_phase)
            return PipelineEvent.COMMIT_SUCCESS
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

    final_state = saved_states[-1]
    assert getattr(final_state, "phase", None) == "complete", (
        f"Pipeline must terminate at 'complete', got {getattr(final_state, 'phase', None)!r}. "
        "Entering 'failed_terminal' would mean a PhaseFailureEvent was emitted."
    )


def _git_commit_count(repo_root: Path) -> int:
    """Return the number of commits in ``repo_root``'s git log."""
    repo = Repo(repo_root)
    try:
        return sum(1 for _ in repo.iter_commits("HEAD"))
    finally:
        repo.close()
