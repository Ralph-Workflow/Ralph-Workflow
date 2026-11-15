from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from git import Repo

from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.completion_receipts import read_artifact_submission_snapshot
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.phases.commit_review import (
    CommitReviewGateFailed,
    CommitReviewRejected,
    run_commit_review_stage1_preflight,
)
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import CommitReviewRejectEffect, ExitFailureEffect, InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]

DEFAULTS = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
RECEIPT_SECRET = "planned-deletion-receipt-secret"


@pytest.fixture(autouse=True)
def parent_receipt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPH_BROKER_SECRET", RECEIPT_SECRET)


def submit_plan(repo_root: Path, content: str, run_id: str) -> None:
    submit_artifact_canonical(
        repo_root,
        "plan",
        {},
        markdown=content,
        run_id=run_id,
        deps=ArtifactHandlerDeps(receipt_secret=RECEIPT_SECRET),
    )


def prepare_deletions(repo_root: Path, paths: list[str]) -> None:
    repo = Repo(repo_root)
    try:
        readme = repo_root / "README.md"
        if readme.exists():
            repo.index.remove(["README.md"], working_tree=True)
        files = [*paths, "legacy/keep.txt", *[f"keep_{index}.txt" for index in range(6)]]
        for relative in files:
            target = repo_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("content\n", encoding="utf-8")
        repo.index.add(files)
        repo.index.commit("initial")
        repo.index.remove(paths, working_tree=True)
    finally:
        repo.close()


def plan(paths: list[str]) -> str:
    items = "\n".join(f"- `{path}`" for path in paths)
    return f"---\ntype: plan\n---\n\n## Approved deletions\n\n{items}\n"


def preflight(repo_root: Path, run_id: str) -> object:
    bundle = load_policy(DEFAULTS)
    return run_commit_review_stage1_preflight(
        repo_root,
        run_id,
        "1",
        bundle.pipeline,
        "development_commit_review",
        artifacts_policy=bundle.artifacts,
    )


def routed_preflight(repo_root: Path) -> object:
    bundle = load_policy(DEFAULTS)
    return determine_effect_from_policy(
        PipelineState(phase="development_commit_review"),
        bundle,
        WorkspaceScope(repo_root),
    )


def assert_mass_deletion_rejected(result: object) -> None:
    assert isinstance(result, CommitReviewRejected), result
    assert "Mass deletion" in result.reason


def test_no_approved_list_fires_mass_deletion_tripwire(tmp_git_repo: Path) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)

    assert_mass_deletion_rejected(preflight(tmp_git_repo, "run-no-plan"))


def test_receipt_bound_approved_list_bypasses_mass_deletion_only(tmp_git_repo: Path) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "run-plan")

    result = preflight(tmp_git_repo, "run-plan")

    assert not isinstance(result, CommitReviewRejected), result


def test_effect_router_regression_receipt_bound_deletions_reach_production_preflight(
    tmp_git_repo: Path,
) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "run")

    result = routed_preflight(tmp_git_repo)

    assert isinstance(result, InvokeAgentEffect), result


def test_production_lookup_uses_authenticated_planning_session_receipt_identity(
    tmp_git_repo: Path,
) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "planning-session-7f3a")

    result = routed_preflight(tmp_git_repo)

    assert isinstance(result, InvokeAgentEffect), result


def test_retained_receipt_cannot_authorize_different_current_canonical_plan(
    tmp_git_repo: Path,
) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "older-planning-session")
    (tmp_git_repo / ".agent/artifacts/plan.md").write_text(
        plan(["unrelated/file.py"]), encoding="utf-8"
    )

    assert_mass_deletion_rejected(preflight(tmp_git_repo, "current-proposal"))


def test_listed_protected_deletion_still_hard_rejects(tmp_git_repo: Path) -> None:
    paths = ["pyproject.toml", "legacy/0.py", "legacy/1.py", "legacy/2.py"]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "run-protected")

    result = preflight(tmp_git_repo, "run-protected")

    assert isinstance(result, CommitReviewRejected), result
    assert "manifest or lockfile" in result.reason


def test_effect_router_regression_listed_protected_deletion_still_rejects(
    tmp_git_repo: Path,
) -> None:
    paths = ["pyproject.toml", "legacy/0.py", "legacy/1.py", "legacy/2.py"]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "run")

    result = routed_preflight(tmp_git_repo)

    assert isinstance(result, CommitReviewRejectEffect), result
    assert "manifest or lockfile" in result.reason


def test_approved_deletion_pattern_is_ignored_as_nonliteral_path(tmp_git_repo: Path) -> None:
    paths = ["src/*", "src/?", "src/[x]", "src/{x}"]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "run")

    result = routed_preflight(tmp_git_repo)

    assert isinstance(result, CommitReviewRejectEffect), result
    assert "Mass deletion" in result.reason


@pytest.mark.parametrize("mode", ["receiptless", "digest-mismatch"])
def test_invalid_plan_authorization_is_treated_as_no_list(
    tmp_git_repo: Path,
    mode: str,
) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)
    if mode == "receiptless":
        target = tmp_git_repo / ".agent" / "PLAN.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(plan(paths), encoding="utf-8")
    else:
        receipt_run = "planning-session-current"
        submit_plan(tmp_git_repo, plan(paths), receipt_run)
        if mode == "digest-mismatch":
            snapshot = read_artifact_submission_snapshot(
                tmp_git_repo,
                receipt_run,
                "plan",
                receipt_secret=RECEIPT_SECRET,
            )
            assert snapshot is not None
            snapshot_path, _snapshot_digest = snapshot
            (tmp_git_repo / snapshot_path).write_text("tampered\n", encoding="utf-8")

    assert_mass_deletion_rejected(preflight(tmp_git_repo, "run-current"))


def test_reviewer_or_developer_artifacts_cannot_widen_approved_list(tmp_git_repo: Path) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)
    artifact_dir = tmp_git_repo / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.joinpath("commit_review.md").write_text(plan(paths), encoding="utf-8")
    tmp_git_repo.joinpath(".agent", "PLAN.md").write_text(plan(paths), encoding="utf-8")

    assert_mass_deletion_rejected(preflight(tmp_git_repo, "run-reviewer-input"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("hmac", "wrong"),
        ("submission_snapshot_path", ".agent/PLAN.md"),
        ("submission_snapshot_path", ".agent/artifacts/plan.md"),
        ("submission_snapshot_path", "../outside.md"),
    ],
)
def test_tampered_receipt_authentication_or_path_yields_no_list(
    tmp_git_repo: Path,
    field: str,
    value: str,
) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "run-current")
    with sqlite3.connect(tmp_git_repo / ".agent/state.db") as connection:
        connection.execute(
            f"UPDATE receipts SET {field} = ? WHERE run_id = ? AND artifact_type = ?",
            (value, "run-current", "plan"),
        )
        connection.commit()
    assert_mass_deletion_rejected(preflight(tmp_git_repo, "run-current"))


def test_symlinked_submission_snapshot_yields_no_list(tmp_git_repo: Path) -> None:
    paths = [f"legacy/{index}.py" for index in range(4)]
    prepare_deletions(tmp_git_repo, paths)
    submit_plan(tmp_git_repo, plan(paths), "run-current")
    snapshot = read_artifact_submission_snapshot(
        tmp_git_repo, "run-current", "plan", receipt_secret=RECEIPT_SECRET
    )
    assert snapshot is not None
    relative_path, _digest = snapshot
    target = tmp_git_repo / relative_path
    redirected = tmp_git_repo / ".agent/PLAN.md"
    target.unlink()
    target.symlink_to(redirected)
    assert_mass_deletion_rejected(preflight(tmp_git_repo, "run-current"))


def test_effect_router_does_not_invoke_reviewer_after_stage1_gate_failure(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_deletions(tmp_git_repo, ["legacy/0.py"])

    def fail_preflight(*_args: object, **_kwargs: object) -> CommitReviewGateFailed:
        return CommitReviewGateFailed(detail="receipt evidence unavailable")

    monkeypatch.setattr(
        "ralph.phases.commit_review.run_commit_review_stage1_preflight",
        fail_preflight,
    )
    result = routed_preflight(tmp_git_repo)
    assert isinstance(result, ExitFailureEffect)
    assert "receipt evidence unavailable" in result.reason
