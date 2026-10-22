"""Every way a feature branch can stand to its target, driven through real git.

The planning gate, the idempotent land and the planning integration entry
point must agree for each relation. The regression this guards: the feature
sat fully inside a target that had moved ahead, the land shortcut answered
"contained" while the gate answered "behind", and planning blocked forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.git.branch_relation import BranchRelation, branch_relation
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_resolution_state import contained_land

_TARGET = "main"


def _git(repo: Path, *args: str) -> None:
    result = run_git(args, cwd=repo, label="test-branch-relation")
    assert result.returncode == 0, result.stderr


def _commit(repo: Path, name: str) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "-m", name)


def _build(repo: Path, relation: BranchRelation) -> None:
    """Leave ``repo`` checked out on ``feature`` standing in ``relation`` to main."""
    _git(repo, "branch", "-M", _TARGET)
    _git(repo, "checkout", "-q", "-b", "feature")
    if relation in {BranchRelation.FEATURE_AHEAD, BranchRelation.DIVERGED}:
        _commit(repo, "feature.txt")
    if relation in {BranchRelation.TARGET_AHEAD, BranchRelation.DIVERGED}:
        _git(repo, "checkout", "-q", _TARGET)
        _commit(repo, "target.txt")
        _git(repo, "checkout", "-q", "feature")


_KNOWN = [
    BranchRelation.SYNCED,
    BranchRelation.TARGET_AHEAD,
    BranchRelation.FEATURE_AHEAD,
    BranchRelation.DIVERGED,
]


@pytest.mark.parametrize("relation", _KNOWN)
def test_branch_relation_names_each_shape(tmp_git_repo: Path, relation: BranchRelation) -> None:
    _build(tmp_git_repo, relation)

    assert branch_relation(tmp_git_repo, _TARGET) is relation


def test_branch_relation_is_unknown_for_a_missing_target(tmp_git_repo: Path) -> None:
    assert branch_relation(tmp_git_repo, "no-such-branch") is BranchRelation.UNKNOWN


def test_relation_needs_follow_the_two_questions() -> None:
    assert {r for r in BranchRelation if r.needs_catch_up} == {
        BranchRelation.TARGET_AHEAD,
        BranchRelation.DIVERGED,
        BranchRelation.UNKNOWN,
    }
    assert {r for r in BranchRelation if r.needs_landing} == {
        BranchRelation.FEATURE_AHEAD,
        BranchRelation.DIVERGED,
        BranchRelation.UNKNOWN,
    }


@pytest.mark.parametrize("relation", _KNOWN)
def test_land_shortcuts_answer_only_when_their_own_question_is_settled(
    tmp_git_repo: Path, relation: BranchRelation
) -> None:
    """A plain land answers once nothing needs landing; a catch-up land needs sync."""
    _build(tmp_git_repo, relation)

    plain = contained_land(tmp_git_repo, _TARGET)
    catch_up = contained_land(tmp_git_repo, _TARGET, require_in_sync=True)

    assert (plain is not None) is (not relation.needs_landing)
    assert (catch_up is not None) is (relation is BranchRelation.SYNCED)
