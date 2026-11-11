from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from git import Repo

if TYPE_CHECKING:
    from pathlib import Path

from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.git.commit_review import untrack_engine_internal_files
from ralph.git.proposal_seal import seal_pending
from ralph.mcp.artifacts._commit_review import CommitReview
from ralph.mcp.artifacts._commit_review_action import CommitReviewAction
from ralph.phases import PhaseContext
from ralph.phases.commit_review import (
    CommitReviewApproved,
    handle_commit_review_phase,
    is_agent_internal_path,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace

pytestmark = [
    pytest.mark.timeout_seconds(10),
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
]

ORIGINALLY_FAILING_TRACKED_PATHS: tuple[str, ...] = (
    "checkpoint.json",
    ".agent/raw/opencode.log",
    ".agent/tmp/mcp-server.log",
    ".agent/checkpoint.json",
    ".agent/workers/unit-a/output.log",
)

EXPECTED_GITIGNORE_FRAGMENTS: tuple[str, ...] = (
    ".agent/",
    "/checkpoint.json",
    "*.cache",
)

EXPECTED_GIT_EXCLUDE_FRAGMENTS: tuple[str, ...] = (
    ".agent/raw/",
    ".agent/tmp/",
    ".agent/completion_seen_*.json",
    "/checkpoint.json",
    ".env.local",
)

INNOCENT_SYMLINK = "innocent_link"
INNOCENT_SYMLINK_TARGET = "some_real_file.txt"


def _track_and_commit(repo_root: Path, rel_path: str) -> None:
    repo = Repo(repo_root)
    try:
        repo.index.add([rel_path])
        repo.index.commit(f"track {rel_path}")
    finally:
        repo.close()


def _pre_stage_tracked_paths(tmp_git_repo: Path) -> None:
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
    target = tmp_git_repo / INNOCENT_SYMLINK_TARGET
    target.write_text("I am a real file, not a target of the symlink chain\n")
    symlink = tmp_git_repo / INNOCENT_SYMLINK
    symlink.symlink_to(target)


def _write_artifact(workspace_root: Path, cleanup: CommitReview, tree_sha: str) -> None:
    artifact_path = workspace_root / ".agent" / "artifacts" / "commit_review.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: commit_review",
        f"verdict: {cleanup.verdict}",
        f"tree_sha: {tree_sha}",
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
    _pre_stage_tracked_paths(tmp_git_repo)
    _pre_stage_innocent_symlink(tmp_git_repo)
    untrack_engine_internal_files(tmp_git_repo, is_agent_internal_path)
    auto_seed_default_gitignore(tmp_git_repo)
    auto_seed_default_git_exclude(tmp_git_repo)
    judged_tree_sha = seal_pending(tmp_git_repo, "run", 1).tree_sha

    cleanup_actions: list[CommitReviewAction] = [
        CommitReviewAction(action="delete_file", path=path)
        for path in ORIGINALLY_FAILING_TRACKED_PATHS
    ]
    cleanup_actions.append(CommitReviewAction(action="add_to_gitignore", pattern="*.cache"))
    cleanup_actions.append(CommitReviewAction(action="add_to_git_exclude", pattern=".env.local"))
    cleanup = CommitReview.model_construct(verdict="approve", actions=cleanup_actions)

    workspace = FsWorkspace(tmp_git_repo)
    _write_artifact(tmp_git_repo, cleanup, judged_tree_sha)

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
