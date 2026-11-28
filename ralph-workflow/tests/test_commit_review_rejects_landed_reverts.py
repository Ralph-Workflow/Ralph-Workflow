from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.git.commit_review import untrack_engine_internal_files
from ralph.git.proposal_seal import seal_pending
from ralph.phases import PhaseContext
from ralph.phases.commit_review import (
    CommitReviewApproved,
    CommitReviewRejected,
    handle_commit_review_phase,
    is_agent_internal_path,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace

pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]

_ARTIFACT_PATH = ".agent/artifacts/commit_review.md"


def _approve(repo_root: Path) -> None:
    untrack_engine_internal_files(repo_root, is_agent_internal_path)
    auto_seed_default_gitignore(repo_root)
    auto_seed_default_git_exclude(repo_root)
    tree_sha = seal_pending(repo_root, "run", 1).tree_sha
    path = repo_root / _ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: commit_review\nverdict: approve\ntree_sha: {tree_sha}\n---\n",
        encoding="utf-8",
    )


def _run_phase(repo_root: Path) -> list[object]:
    context = PhaseContext.construct(
        workspace=FsWorkspace(repo_root),
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
        run_id="phase-test-run",
        iteration="1",
    )
    return handle_commit_review_phase(effect, context)


def _tracked(repo_root: Path, rel_path: str, text: str) -> Path:
    path = repo_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    repo = Repo(repo_root)
    try:
        repo.index.add([rel_path])
        repo.index.commit(f"track {rel_path}")
    finally:
        repo.close()
    return path


def _land_from_another_run(repo_root: Path, files: dict[str, str | None], subject: str) -> None:
    other_root = repo_root.parent / f"{repo_root.name}-other-run"
    other = Repo.clone_from(str(repo_root), str(other_root))
    try:
        other.git.config("user.name", "Other Run")
        other.git.config("user.email", "other-run@example.invalid")
        for rel_path, text in files.items():
            if text is None:
                other.index.remove([rel_path], working_tree=True)
                continue
            path = other_root / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            other.index.add([rel_path])
        other.index.commit(subject)
    finally:
        other.close()
    repo = Repo(repo_root)
    try:
        repo.git.pull("--ff-only", str(other_root), "HEAD")
    finally:
        repo.close()


def _land_here(repo_root: Path, files: dict[str, str], subject: str) -> None:
    repo = Repo(repo_root)
    try:
        for rel_path, text in files.items():
            path = repo_root / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            repo.index.add([rel_path])
        repo.index.commit(subject)
    finally:
        repo.close()


def test_a_proposal_that_undoes_a_landed_commit_is_rejected(tmp_git_repo: Path) -> None:
    _tracked(tmp_git_repo, "app/console.ts", "export const step = 1;\n")
    _land_from_another_run(
        tmp_git_repo,
        {"app/console.ts": "export const step = 2;\n", "app/MachinePick.tsx": "export {};\n"},
        "feat(connect): let members choose a machine",
    )
    (tmp_git_repo / "app/console.ts").write_text("export const step = 1;\n", encoding="utf-8")
    (tmp_git_repo / "app/MachinePick.tsx").unlink()
    _approve(tmp_git_repo)

    events = _run_phase(tmp_git_repo)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is True
    assert "feat(connect): let members choose a machine" in event.reason
    assert "app/MachinePick.tsx" in event.reason
    assert "app/console.ts" in event.reason


def test_a_proposal_that_builds_on_a_landed_commit_is_still_approved(tmp_git_repo: Path) -> None:
    _tracked(tmp_git_repo, "app/console.ts", "export const step = 1;\n")
    _land_from_another_run(
        tmp_git_repo, {"app/console.ts": "export const step = 2;\n"}, "feat: step two"
    )
    (tmp_git_repo / "app/console.ts").write_text("export const step = 3;\n", encoding="utf-8")
    _approve(tmp_git_repo)

    events = _run_phase(tmp_git_repo)

    assert [type(event) for event in events] == [CommitReviewApproved]


def test_a_run_that_reverts_its_own_landed_commit_is_still_approved(tmp_git_repo: Path) -> None:
    _tracked(tmp_git_repo, "app/console.ts", "export const step = 1;\n")
    _land_here(tmp_git_repo, {"app/console.ts": "export const step = 2;\n"}, "feat: step two")
    (tmp_git_repo / "app/console.ts").write_text("export const step = 1;\n", encoding="utf-8")
    _approve(tmp_git_repo)

    events = _run_phase(tmp_git_repo)

    assert [type(event) for event in events] == [CommitReviewApproved]


def test_deleting_a_file_another_run_added_is_still_approved(tmp_git_repo: Path) -> None:
    _tracked(tmp_git_repo, "app/console.ts", "export const step = 1;\n")
    _land_from_another_run(
        tmp_git_repo, {"app/obsolete.ts": "export {};\n"}, "feat: add an obsolete helper"
    )
    (tmp_git_repo / "app/obsolete.ts").unlink()
    _approve(tmp_git_repo)

    events = _run_phase(tmp_git_repo)

    assert [type(event) for event in events] == [CommitReviewApproved]
