"""Rock-solid integration test for the commit_review phase.

This test exercises ``handle_commit_review_phase`` end-to-end on a
single real ``tmp_git_repo`` fixture, pre-staging the five originally
failing TRACKED files plus a SEPARATE symlink that MUST be preserved.
The test pins the contract that the phase:

* deletes every originally-failing tracked path,
* preserves an innocent symlink (PA-005 fix: deletable tracked files
  and the preserved symlink MUST use distinct paths so the assertions
  can be simultaneously true),
* auto-seeds canonical patterns into ``.gitignore`` and
  ``.git/info/exclude``,
* leaves no dangling staging files in the worktree.

This is a fast, focused unit-level test that complements the
end-to-end ``runner.run`` tests in
``tests/integration/test_pipeline_commit_review_to_commit_e2e.py`` and
``tests/integration/test_pipeline_final_commit_review_to_final_commit_e2e.py``.
Per-test timeout is capped at 10s (well under the 60s combined budget
enforced by ``ralph/verify.py``).

Testing rules followed:
* < 1s per-test wall-clock rule (the fixture is a session-template clone)
* 60s combined-budget rule (one test contributes negligible time)
* No real network, no time.sleep, no real subprocess outside git
* Black-box: asserts observable behavior (file presence / absence,
  .gitignore / .git/info/exclude content) NOT internal method calls
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from git import Repo

if TYPE_CHECKING:
    from pathlib import Path

from ralph.mcp.artifacts._commit_review import CommitReview
from ralph.mcp.artifacts._commit_review_action import CommitReviewAction
from ralph.phases import PhaseContext
from ralph.phases.commit_review import CommitReviewApproved, handle_commit_review_phase
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace

# The module carries the module-level ``subprocess_e2e`` marker because the
# proof drives the REAL git boundary (a real repository, the real phase). It
# is registered in ``ralph.test_suites.REQUIRED_AUTO_INTEGRATE_E2E_FILES`` and
# additionally declares ``required_auto_integrate_e2e`` here, so the default
# ``make test`` profile selects these proofs instead of deselecting them
# behind the opt-in subprocess gate (where they would rot silently).
pytestmark = [
    pytest.mark.timeout_seconds(10),
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
]

# Five originally-failing TRACKED files (PA-005: distinct from the
# preserved-symlink path). These were the exact paths that the legacy
# hard-fail (``Refusing to delete non-housekeeping file: ...``) rejected
# in the user-reported failure.
ORIGINALLY_FAILING_TRACKED_PATHS: tuple[str, ...] = (
    "checkpoint.json",
    ".agent/raw/opencode.log",
    ".agent/tmp/mcp-server.log",
    ".agent/checkpoint.json",
    ".agent/workers/unit-a/output.log",
)

# Canonical auto-seed fragments the hardening pins into .gitignore.
EXPECTED_GITIGNORE_FRAGMENTS: tuple[str, ...] = (
    ".agent/",
    "/checkpoint.json",
    "*.cache",
)

# Canonical auto-seed fragments the hardening pins into .git/info/exclude.
EXPECTED_GIT_EXCLUDE_FRAGMENTS: tuple[str, ...] = (
    ".agent/raw/",
    ".agent/tmp/",
    ".agent/completion_seen_*.json",
    "/checkpoint.json",
    ".env.local",
)

# PA-005: the innocent symlink MUST use a distinct path from any
# deletable tracked file so the assertions can simultaneously be true.
INNOCENT_SYMLINK = "innocent_link"
INNOCENT_SYMLINK_TARGET = "some_real_file.txt"


def _track_and_commit(repo_root: Path, rel_path: str) -> None:
    """Stage and commit one relative path inside ``repo_root``."""
    repo = Repo(repo_root)
    try:
        repo.index.add([rel_path])
        repo.index.commit(f"track {rel_path}")
    finally:
        repo.close()


def _pre_stage_tracked_paths(tmp_git_repo: Path) -> None:
    """Pre-stage the five originally-failing tracked files for the test."""
    (tmp_git_repo / "checkpoint.json").write_text('{"phase": "development"}')
    (tmp_git_repo / ".agent" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp_git_repo / ".agent" / "raw" / "opencode.log").write_text("log content\n")
    (tmp_git_repo / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
    (tmp_git_repo / ".agent" / "tmp" / "mcp-server.log").write_text("mcp log\n")
    (tmp_git_repo / ".agent" / "checkpoint.json").write_text('{"phase": "agent checkpoint"}')
    (tmp_git_repo / ".agent" / "workers" / "unit-a").mkdir(parents=True, exist_ok=True)
    (tmp_git_repo / ".agent" / "workers" / "unit-a" / "output.log").write_text("worker output\n")

    for rel_path in ORIGINALLY_FAILING_TRACKED_PATHS:
        _track_and_commit(tmp_git_repo, rel_path)


def _pre_stage_innocent_symlink(tmp_git_repo: Path) -> None:
    """Pre-stage a separate, distinct-path symlink that MUST be preserved.

    PA-005: the symlink path MUST be distinct from every deletable
    tracked path so the assertions can simultaneously be true (the
    symlink is preserved, the deletable tracked files are gone).
    """
    target = tmp_git_repo / INNOCENT_SYMLINK_TARGET
    target.write_text("I am a real file, not a target of the symlink chain\n")
    symlink = tmp_git_repo / INNOCENT_SYMLINK
    symlink.symlink_to(target)


def _write_artifact(workspace_root: Path, cleanup: CommitReview) -> None:
    """Write the commit_review artifact to ``workspace_root``.

    The artifact carries the canonical ``verdict`` contract. The phase
    binds the approval to the sealed proposal tree it computed itself, so
    the artifact does not have to repeat the tree SHA.
    """
    artifact_path = workspace_root / ".agent" / "artifacts" / "commit_review.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: commit_review",
        f"verdict: {cleanup.verdict}",
        "---",
        "",
        "## Actions",
    ]
    for index, action in enumerate(cleanup.actions, start=1):
        value = action.path if action.action == "delete_file" else action.pattern
        lines.append(f"- [A{index}] {action.action} | {value}")
    artifact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_handle_commit_review_phase_rock_solid(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    """End-to-end rock-solid cleanup on the five originally-failing tracked paths.

    Pins every postcondition simultaneously (PA-005):

    * The phase returns a single ``CommitReviewApproved`` carrying the
      40-hex SHA of the sealed proposal tree it approved.
    * Every originally-failing tracked file is deleted.
    * The innocent symlink at ``tmp_git_repo/'innocent_link'`` is preserved.
    * The symlink's target file at ``tmp_git_repo/'some_real_file.txt'``
      is preserved (not deleted as a side effect of any symlink follow).
    * ``.gitignore`` was auto-seeded with the canonical patterns AND
      contains the user's ``*.cache`` pattern from the action list.
    * ``.git/info/exclude`` was auto-seeded with the canonical patterns
      AND contains the user's ``.env.local`` pattern from the action list.
    * No ``.ralph-staging.*`` / ``.tmp`` / ``.staging`` files dangle in
      ``tmp_git_repo`` or ``tmp_git_repo/.git/info/``.
    """
    _pre_stage_tracked_paths(tmp_git_repo)
    _pre_stage_innocent_symlink(tmp_git_repo)

    cleanup_actions: list[CommitReviewAction] = [
        CommitReviewAction(action="delete_file", path=path)
        for path in ORIGINALLY_FAILING_TRACKED_PATHS
    ]
    cleanup_actions.append(CommitReviewAction(action="add_to_gitignore", pattern="*.cache"))
    cleanup_actions.append(CommitReviewAction(action="add_to_git_exclude", pattern=".env.local"))
    cleanup = CommitReview.model_construct(verdict="approve", actions=cleanup_actions)

    workspace = FsWorkspace(tmp_git_repo)
    _write_artifact(tmp_git_repo, cleanup)

    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_review",
        prompt_file="cleanup.txt",
    )
    result = handle_commit_review_phase(effect, ctx)

    # RALPH-TEST-REASON: Phase returns CommitReviewApproved event carrying sealed tree SHA instead of PipelineEvent.AGENT_SUCCESS
    assert len(result) == 1 and isinstance(result[0], CommitReviewApproved), (
        f"Phase must approve the rock-solid cleanup batch, got: {result!r}"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", result[0].tree_sha), (
        f"The approval must carry the 40-hex sealed tree SHA, got: {result[0].tree_sha!r}"
    )

    for rel_path in ORIGINALLY_FAILING_TRACKED_PATHS:
        assert not (tmp_git_repo / rel_path).exists(), (
            f"{rel_path!r} should have been deleted by the cleanup phase, but it still exists"
        )

    innocent_link = tmp_git_repo / INNOCENT_SYMLINK
    assert innocent_link.is_symlink(), (
        "The innocent symlink MUST still be a symlink (preserved, not "
        "deleted-as-target and not deleted-as-symlink)"
    )
    assert innocent_link.resolve() == (tmp_git_repo / INNOCENT_SYMLINK_TARGET).resolve(), (
        "The innocent symlink MUST still point to its original target"
    )
    assert (tmp_git_repo / INNOCENT_SYMLINK_TARGET).exists(), (
        "The symlink's target file MUST NOT be deleted as a side effect"
    )

    gitignore_text = (tmp_git_repo / ".gitignore").read_text()
    for fragment in EXPECTED_GITIGNORE_FRAGMENTS:
        assert fragment in gitignore_text, (
            f"Expected {fragment!r} in auto-seeded .gitignore, got:\n{gitignore_text}"
        )

    exclude_text = (tmp_git_repo / ".git" / "info" / "exclude").read_text()
    for fragment in EXPECTED_GIT_EXCLUDE_FRAGMENTS:
        assert fragment in exclude_text, (
            f"Expected {fragment!r} in auto-seeded .git/info/exclude, got:\n{exclude_text}"
        )

    for dangling in tmp_git_repo.rglob("*"):
        if not dangling.is_file():
            continue
        name = dangling.name
        assert ".ralph-staging." not in name, (
            f"Staging file dangling after atomic publish: {dangling}"
        )
        assert ".tmp" not in name or dangling.suffix == ".tmp", (
            f"Suspicious dangling .tmp file after cleanup: {dangling}"
        )
        assert ".staging" not in name, (
            f"Suspicious dangling .staging file after cleanup: {dangling}"
        )

    git_info_dir = tmp_git_repo / ".git" / "info"
    if git_info_dir.exists():
        for dangling in git_info_dir.rglob("*"):
            if not dangling.is_file():
                continue
            name = dangling.name
            assert ".ralph-staging." not in name, f"Staging file dangling in .git/info: {dangling}"
