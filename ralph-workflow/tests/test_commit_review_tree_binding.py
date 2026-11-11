from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.git.operations import get_head_sha, has_commits_since
from ralph.git.proposal_seal import seal_pending
from ralph.phases import PhaseContext
from ralph.phases.commit_review import (
    COMMIT_REVIEW_ARTIFACT_PATH,
    CommitReviewApproved,
    CommitReviewGateFailed,
    CommitReviewLoopback,
    CommitReviewRejected,
    handle_commit_review_phase,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace

pytestmark = [pytest.mark.timeout_seconds(5)]


def _seed_engine_defaults(repo_root: Path) -> None:
    auto_seed_default_gitignore(repo_root)
    auto_seed_default_git_exclude(repo_root)


def _ctx(workspace: FsWorkspace) -> PhaseContext:
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )


def _invoke(phase: str = "development_commit_review") -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="dev", phase=phase, prompt_file="cleanup.txt")


def _run(workspace: FsWorkspace, phase: str = "development_commit_review") -> list[object]:
    return handle_commit_review_phase(_invoke(phase), _ctx(workspace))


def _write_commit_review_artifact(
    workspace: FsWorkspace,
    content: dict[str, object],
) -> None:
    verdict = str(content.get("verdict", "approve"))
    tree_sha = content.get("tree_sha")

    lines = [
        "---",
        "type: commit_review",
        f"verdict: {verdict}",
    ]
    if tree_sha is not None:
        lines.append(f"tree_sha: {tree_sha}")
    lines.append("---")

    actions = content.get("actions", [])
    if actions:
        lines.append("## Actions")
        assert isinstance(actions, list)
        for index, action in enumerate(actions, start=1):
            assert isinstance(action, dict)
            action_name = action["action"]
            value = action.get("path", action.get("pattern"))
            lines.append(f"- [A{index}] {action_name} | {value}")

    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_unbound_artifact(
    workspace: FsWorkspace,
    *,
    tree_sha: str | None = None,
    verdict: str = "approve",
) -> None:
    lines = [
        "---",
        "type: commit_review",
        f"verdict: {verdict}",
    ]
    if tree_sha is not None:
        lines.append(f"tree_sha: {tree_sha}")
    lines.extend(["---", "", "## Actions"])
    path = Path(workspace.root) / COMMIT_REVIEW_ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_artifact_without_tree_sha_is_never_approved(tmp_git_repo: Path) -> None:
    pending = tmp_git_repo / "pending_work.txt"
    pending.write_text("work in progress\n", encoding="utf-8")
    _seed_engine_defaults(tmp_git_repo)

    workspace = FsWorkspace(tmp_git_repo)
    _write_unbound_artifact(workspace)

    head_before = get_head_sha(tmp_git_repo)

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewLoopback), (
        f"An unbound verdict must be re-reviewed, not approved: {event!r}"
    )
    assert "tree_sha" in event.hint
    assert not workspace.exists(COMMIT_REVIEW_ARTIFACT_PATH)
    assert pending.read_text(encoding="utf-8") == "work in progress\n"
    assert get_head_sha(tmp_git_repo) == head_before
    assert not has_commits_since(tmp_git_repo, head_before)


def test_artifact_with_an_invalid_tree_sha_is_never_approved(tmp_git_repo: Path) -> None:
    pending = tmp_git_repo / "pending_work.txt"
    pending.write_text("work in progress\n", encoding="utf-8")
    _seed_engine_defaults(tmp_git_repo)

    workspace = FsWorkspace(tmp_git_repo)
    _write_unbound_artifact(workspace, tree_sha="not-a-tree-sha")

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewLoopback), (
        f"An invalid tree binding must be re-reviewed, not approved: {event!r}"
    )
    assert "not-a-tree-sha" in event.hint
    assert "Invalid" in event.hint, (
        f"A malformed tree SHA must be reported as invalid, not as stale: {event.hint!r}"
    )
    assert pending.read_text(encoding="utf-8") == "work in progress\n"


def test_a_reject_without_tree_sha_is_never_a_rollback(tmp_git_repo: Path) -> None:
    pending = tmp_git_repo / "src" / "app.py"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("print('hello')\n", encoding="utf-8")
    _seed_engine_defaults(tmp_git_repo)

    workspace = FsWorkspace(tmp_git_repo)
    _write_unbound_artifact(workspace, verdict="reject")

    head_before = get_head_sha(tmp_git_repo)

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert not isinstance(event, CommitReviewRejected), (
        f"An unbound verdict must never authorise a rollback: {event!r}"
    )
    assert isinstance(event, CommitReviewLoopback), (
        f"An unbound verdict must be re-reviewed: {event!r}"
    )
    assert pending.read_text(encoding="utf-8") == "print('hello')\n"
    assert get_head_sha(tmp_git_repo) == head_before
    assert not has_commits_since(tmp_git_repo, head_before)


def test_an_unbound_verdict_is_bounded_by_the_no_progress_budget(tmp_git_repo: Path) -> None:
    pending = tmp_git_repo / "pending_work.txt"
    pending.write_text("work in progress\n", encoding="utf-8")
    _seed_engine_defaults(tmp_git_repo)

    workspace = FsWorkspace(tmp_git_repo)
    events: list[object] = []
    for _ in range(4):
        _write_unbound_artifact(workspace)
        events.append(_run(workspace)[0])

    assert not any(
        isinstance(event, (CommitReviewApproved, CommitReviewRejected)) for event in events
    ), f"An unbound verdict must never approve and never reject: {events!r}"
    assert isinstance(events[-1], CommitReviewGateFailed), (
        f"The identical no-progress bound must fail the gate, got: {events[-1]!r}"
    )
    assert pending.read_text(encoding="utf-8") == "work in progress\n"


def test_the_edit_step_reseal_still_approves_the_post_edit_tree(tmp_git_repo: Path) -> None:
    junk = tmp_git_repo / "session-output.txt"
    junk.write_text("tmp\n", encoding="utf-8")
    _seed_engine_defaults(tmp_git_repo)
    judged = seal_pending(tmp_git_repo, "run", 1)

    workspace = FsWorkspace(tmp_git_repo)
    _write_commit_review_artifact(
        workspace,
        {
            "verdict": "approve",
            "tree_sha": judged.tree_sha,
            "actions": [{"action": "delete_file", "path": "session-output.txt"}],
        },
    )

    result = _run(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewApproved), (
        f"The reseal flow must still approve the post-edit tree: {event!r}"
    )
    assert event.tree_sha != judged.tree_sha
    resealed = seal_pending(tmp_git_repo, "run", 1)
    assert event.tree_sha == resealed.tree_sha
    assert not junk.exists()
