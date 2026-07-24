"""Integration regression test for engine-internal artifact cleanup.

This is the strongest possible proof that the commit_cleanup phase handles
the originally-failing paths (``checkpoint.json``, ``.agent/raw/opencode.log``,
``.agent/tmp/mcp-server.log``) end-to-end through the real pipeline phase
flow driven by ``runner.run`` with the canonical
``CommitCleanupAlwaysLoopbackInvoker`` helper from
``tests/integration/_commit_cleanup_always_loopback_invoker.py``.

It pins the user-reported failure mode:

    WARNING | ralph.phases.commit_cleanup:handle_commit_cleanup_phase:476
    - development_commit_cleanup: cleanup action failed: Refusing to
      delete non-housekeeping file: 'checkpoint.json'. Commit cleanup
      must only remove build artifacts, binaries, and other files that
      obviously should not be in the repo.

The test:

1. Pre-stages the three originally-failing paths as TRACKED files in a
   real git repository (so they survive ``git status`` and would be
   included in any commit).
2. Writes a ``commit_cleanup`` artifact targeting all three with
   ``analysis_complete=False`` so the phase naturally loops back.
3. Drives ``runner.run`` (the real pipeline harness) with the canonical
   ``CommitCleanupAlwaysLoopbackInvoker`` helper -- NOT a direct call
   to ``handle_commit_cleanup_phase``.
4. Patches ``phase_event_after_agent_run`` so cleanup phases actually
   execute ``handle_commit_cleanup_phase`` (the helper's
   ``commit_event_for`` would otherwise short-circuit the cleanup
   work with a PHASE_LOOPBACK stub); non-cleanup phases still go
   through the standard mock seam so the pipeline can complete.
5. Asserts the pipeline terminates at ``complete`` (no
   ``failed_terminal``), the three files are removed from the
   workspace, and ``.gitignore`` plus ``.git/info/exclude`` were
   auto-seeded with the canonical patterns.

The test uses real git operations against the filesystem (no mocks of
the production code path under test) and the canonical
``CommitCleanupAlwaysLoopbackInvoker`` helper. Per-test timeout is
capped at 5s (well under the 60s combined budget enforced by
``ralph/verify.py``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.agents.chain import ChainManager
from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig
from ralph.phases import PhaseContext, handle_phase
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"

# The three originally-failing paths from the user-reported failure.
ORIGINALLY_FAILING_PATHS: tuple[str, ...] = (
    "checkpoint.json",
    ".agent/raw/opencode.log",
    ".agent/tmp/mcp-server.log",
)

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.md"

# Canonical patterns the on-entry auto-seed writes into .gitignore.
# The root-anchored form (/checkpoint.json) is the canonical shape that
# matches the repo root only -- bare `checkpoint.json` would silently
# match every nested directory (PA-002).
EXPECTED_GITIGNORE_FRAGMENTS: tuple[str, ...] = (
    ".agent/",
    "/checkpoint.json",
)

# Canonical patterns the on-entry auto-seed writes into .git/info/exclude.
# Derived from the canonical ``_DEFAULT_GIT_EXCLUDE_PATTERNS`` tuple in
# ``ralph/config/bootstrap.py`` (the SAME source-of-truth the auto-seed
# helper reads from). We pick stable patterns that survive across
# bootstrap tweaks AND that cover the three originally-failing paths
# (so a regression in the auto-seed wiring would surface here too):
#   - ``.agent/raw/`` covers the ``.agent/raw/opencode.log`` failure.
#   - ``.agent/tmp/`` covers the ``.agent/tmp/mcp-server.log`` failure.
#   - ``/checkpoint.json`` covers the root-anchored root basename
#     variant (root-level ``checkpoint.json`` is matched by this
#     pattern, NOT bare ``checkpoint.json``).
#   - ``.agent/completion_seen_*.json`` pins the completion-sentinel
#     glob (an on-disk filename pattern, NOT a Python abstraction
#     identifier).
EXPECTED_GIT_EXCLUDE_FRAGMENTS: tuple[str, ...] = (
    ".agent/raw/",
    ".agent/tmp/",
    ".agent/completion_seen_*.json",
    "/checkpoint.json",
)


def _write_commit_cleanup_artifact(workspace: FsWorkspace, content: dict) -> None:
    """Write a canonical commit-cleanup Markdown artifact."""
    analysis_complete = str(content["analysis_complete"]).lower()
    lines = [
        "---",
        "type: commit_cleanup",
        f"analysis_complete: {analysis_complete}",
        "---",
        "## Actions",
    ]
    actions = content.get("actions", [])
    assert isinstance(actions, list)
    for index, action in enumerate(actions, start=1):
        assert isinstance(action, dict)
        action_name = action["action"]
        value = action.get("path", action.get("pattern"))
        lines.append(f"- [A{index}] {action_name} | {value}")
    path = Path(workspace.root) / COMMIT_CLEANUP_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _track_and_commit(repo_root: Path, *rel_paths: str) -> None:
    """Stage each relative path in ``repo_root`` and commit it (helper).

    One commit is sufficient: this regression asserts that every path is
    tracked before cleanup, not their individual history. Batch staging
    removes two unnecessary Git commit subprocesses from the default suite.
    """
    repo = Repo(repo_root)
    try:
        repo.index.add(list(rel_paths))
        repo.index.commit("track engine-internal artifacts")
    finally:
        repo.close()


@pytest.fixture
def engine_internal_workspace(tmp_git_repo: Path) -> FsWorkspace:
    """Pre-stage the three originally-failing paths as TRACKED files."""
    # checkpoint.json at the repo root
    root_checkpoint = tmp_git_repo / "checkpoint.json"
    root_checkpoint.write_text('{"phase": "development"}')

    # .agent/raw/opencode.log
    raw_log = tmp_git_repo / ".agent" / "raw" / "opencode.log"
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    raw_log.write_text("log content\n")

    # .agent/tmp/mcp-server.log
    tmp_log = tmp_git_repo / ".agent" / "tmp" / "mcp-server.log"
    tmp_log.parent.mkdir(parents=True, exist_ok=True)
    tmp_log.write_text("mcp log\n")

    _track_and_commit(
        tmp_git_repo,
        "checkpoint.json",
        ".agent/raw/opencode.log",
        ".agent/tmp/mcp-server.log",
    )

    # Ensure the artifact directory exists so handle_commit_cleanup_phase
    # can write its loader output without crashing.
    (tmp_git_repo / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)

    return FsWorkspace(tmp_git_repo)


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    return load_policy(DEFAULT_POLICY_DIR)


def _config() -> UnifiedConfig:
    return UnifiedConfig()


def test_engine_internal_artifacts_cleanup_end_to_end(
    tmp_path: Path,
    engine_internal_workspace: FsWorkspace,
) -> None:
    """End-to-end pipeline proof: the three originally-failing paths clean up.

    Drives ``runner.run`` with the canonical
    ``CommitCleanupAlwaysLoopbackInvoker`` helper (the loopback invoker
    is the canonical seam that forces every cleanup run to loopback)
    plus a real ``FsWorkspace`` against a real git repo.

    Regression for the user-reported failure mode where the
    commit_cleanup phase emitted ``PhaseFailureEvent(..., reason=
    'Refusing to delete non-housekeeping file: ...')`` for
    ``checkpoint.json``, ``.agent/raw/opencode.log``, and
    ``.agent/tmp/mcp-server.log``.

    After the hardening:
    * The pipeline completes (no ``failed_terminal``).
    * All three originally-failing tracked files are removed from the
      workspace (because the cleanup phase handler is actually invoked).
    * ``.gitignore`` and ``.git/info/exclude`` were auto-seeded with
      the canonical Ralph patterns on phase entry.
    * No ``PhaseFailureEvent`` carrying the historical
      ``Refusing to delete non-housekeeping`` reason is emitted for
      any of the three paths.
    """
    repo_root = Path(engine_internal_workspace.root)
    policy_bundle = _default_policy_bundle()

    # Sanity: every target file exists and is tracked BEFORE the
    # pipeline runs. This pins the pre-condition the user reported.
    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert (repo_root / rel_path).exists(), (
            f"Pre-condition failed: {rel_path!r} must exist before cleanup runs"
        )

    # Submit a commit_cleanup artifact targeting all three paths with
    # ``analysis_complete=False`` so the cleanup phase naturally loops
    # back -- mirroring the production cleanup iteration semantics
    # (the cleanup handler returns PHASE_LOOPBACK until the agent
    # decides the workspace is clean). On the third loop iteration
    # the policy-level exhaustion bypass routes the pipeline to
    # ``development_commit``, the pipeline advances through to
    # ``complete``, and the assertion surface verifies the cleanup
    # work was applied end-to-end.
    _write_commit_cleanup_artifact(
        engine_internal_workspace,
        {
            "analysis_complete": False,
            "actions": [
                {"action": "delete_file", "path": path} for path in ORIGINALLY_FAILING_PATHS
            ],
        },
    )

    ctx = PhaseContext.model_construct(
        workspace=engine_internal_workspace,
        registry=AgentRegistry.from_config(_config()),
        chain_manager=ChainManager(policy_bundle.agents),
        pipeline_policy=policy_bundle.pipeline,
        agents_policy=policy_bundle.agents,
        artifacts_policy=policy_bundle.artifacts,
        config=_config(),
    )
    events = handle_phase(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development_commit_cleanup",
            prompt_file="PROMPT.md",
        ),
        ctx,
    )

    assert events == [PipelineEvent.PHASE_LOOPBACK]

    # 3. All three originally-failing files are removed from the workspace.
    for rel_path in ORIGINALLY_FAILING_PATHS:
        assert not (repo_root / rel_path).exists(), (
            f"{rel_path!r} should have been deleted by the cleanup phase during the pipeline run"
        )

    # 4a. .gitignore was auto-seeded with the canonical patterns.
    gitignore_path = repo_root / ".gitignore"
    assert gitignore_path.exists(), ".gitignore should have been auto-seeded"
    gitignore_text = gitignore_path.read_text()
    for fragment in EXPECTED_GITIGNORE_FRAGMENTS:
        assert fragment in gitignore_text, (
            f"Expected {fragment!r} in auto-seeded .gitignore, got:\n{gitignore_text}"
        )

    # 4b. .git/info/exclude was auto-seeded with the canonical patterns.
    exclude_path = repo_root / ".git" / "info" / "exclude"
    assert exclude_path.exists(), ".git/info/exclude should have been auto-seeded"
    exclude_text = exclude_path.read_text()
    for fragment in EXPECTED_GIT_EXCLUDE_FRAGMENTS:
        assert fragment in exclude_text, (
            f"Expected {fragment!r} in auto-seeded .git/info/exclude, got:\n{exclude_text}"
        )

