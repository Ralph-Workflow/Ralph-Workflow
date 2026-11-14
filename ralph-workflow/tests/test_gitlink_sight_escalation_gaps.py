from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult

from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.conflict_resolution.deterministic_resolution import (
    try_deterministic_resolution,
)
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStop,
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.sight import (
    AGENT_ELIGIBLE_SIGHTS,
    ConflictSight,
    agent_eligible_paths,
    classify_stage_map,
    classify_unmerged_conflicts,
    policy_gitlink_paths,
)

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Committer",
    "GIT_COMMITTER_EMAIL": "committer@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(cwd: Path, *args: str, input_text: str | None = None) -> GitRunResult:
    result = run_git(
        args,
        cwd=cwd,
        label="gitlink-sight-policy-test",
        options=GitRunOptions(env=_GIT_ENV, input_data=input_text, timeout=5),
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed (code {result.returncode}): {result.stderr}")
    return result


def _repo_with_gitlink_conflict(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "initial")
    base = "1" * 40
    ours = "2" * 40
    theirs = "3" * 40
    index_info = f"160000 {base} 1\tsub\n160000 {ours} 2\tsub\n160000 {theirs} 3\tsub\n"
    _git(repo, "update-index", "--index-info", input_text=index_info)
    return repo, base, ours, theirs


@pytest.mark.timeout_seconds(5)
def test_diverged_gitlink_has_a_policy_owned_non_agent_sight() -> None:
    stages = {
        1: ("160000", "1" * 40),
        2: ("160000", "2" * 40),
        3: ("160000", "3" * 40),
    }

    sight = classify_stage_map(stages, binary=False, has_markers=False)

    assert sight is ConflictSight.GITLINK_POLICY
    assert sight not in AGENT_ELIGIBLE_SIGHTS


@pytest.mark.timeout_seconds(5)
def test_gitlink_mode_mismatch_remains_policy_owned_not_agent_eligible() -> None:
    gitlink_ours = {
        2: ("160000", "a" * 40),
        3: ("040000", "b" * 40),
    }
    gitlink_theirs = {
        2: ("100644", "a" * 40),
        3: ("160000", "b" * 40),
    }

    ours_sight = classify_stage_map(gitlink_ours, binary=False, has_markers=False)
    theirs_sight = classify_stage_map(gitlink_theirs, binary=True, has_markers=False)

    assert ours_sight is ConflictSight.GITLINK_POLICY
    assert theirs_sight is ConflictSight.GITLINK_POLICY
    assert ours_sight not in AGENT_ELIGIBLE_SIGHTS
    assert theirs_sight not in AGENT_ELIGIBLE_SIGHTS


@pytest.mark.timeout_seconds(5)
def test_unmerged_gitlink_classification_exposes_policy_route(
    tmp_path: Path,
) -> None:
    repo, _base, _ours, _theirs = _repo_with_gitlink_conflict(tmp_path)

    kinds = classify_unmerged_conflicts(repo, ("sub",))

    assert kinds == {"sub": ConflictSight.GITLINK_POLICY}
    assert policy_gitlink_paths(kinds) == ("sub",)
    assert agent_eligible_paths(kinds) == ()


@pytest.mark.timeout_seconds(5)
def test_unfetchable_gitlink_pointer_is_resolved_by_policy_without_operator(
    tmp_path: Path,
) -> None:
    repo, _base, ours, _theirs = _repo_with_gitlink_conflict(tmp_path)
    stop = RebaseStop(
        stop_index=1,
        stop_cap=1,
        conflicted_files=("sub",),
        sha="deadbeef",
        subject="diverged gitlink",
    )
    session = ResolutionSession()

    with bind_active_rebase_resolution_session(session):
        resolved = try_deterministic_resolution(repo, stop)

    assert resolved is True
    assert _git(repo, "ls-files", "-u", "sub").stdout.strip() == ""
    assert _git(repo, "ls-files", "-s", "sub").stdout.startswith(f"160000 {ours} 0")
    assert session.terminal_reason is None
    assert session.operator_action_message is None
    assert session.exhaustion_reason is None
