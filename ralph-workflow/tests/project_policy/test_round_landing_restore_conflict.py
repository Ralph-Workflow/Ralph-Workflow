from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.config.models import UnifiedConfig
from ralph.git.merge import unmerged_paths
from ralph.project_policy import round_landing
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState

pytestmark = pytest.mark.subprocess_e2e


def _commit(root: Path, path: str, message: str) -> None:
    repo = Repo(root)
    try:
        repo.index.add([path])
        repo.index.commit(message)
    finally:
        repo.close()


def test_a_restore_that_conflicts_with_the_landed_round_is_resolved_not_left_unmerged(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    policy = tmp_git_repo / "policy.md"
    policy.write_text("census: 11 profiles\n", encoding="utf-8")
    _commit(tmp_git_repo, "policy.md", "track policy")
    policy.write_text("census: development edit\n", encoding="utf-8")

    def _integrate(
        _config: UnifiedConfig, scope: WorkspaceScope, _state: RebaseState, **_kw: object
    ) -> None:
        (scope.root / "policy.md").write_text("census: landed by another run\n", encoding="utf-8")
        _commit(scope.root, "policy.md", "land a conflicting change")

    resolved: list[Path] = []

    def _resolve(root: Path) -> bool:
        resolved.append(root)
        (root / "policy.md").write_text("census: resolved\n", encoding="utf-8")
        return True

    monkeypatch.setattr(round_landing, "auto_integrate_after_commit", _integrate)
    scope = WorkspaceScope(root=str(tmp_git_repo), allowed_roots=[str(tmp_git_repo)])

    landing = round_landing.round_landing(
        conflict_resolver=None, rebase_stop_resolver=None, restore_conflict_resolver=_resolve
    )
    landing(scope, UnifiedConfig(), frozenset({"policy.md"}))

    assert resolved == [tmp_git_repo]
    assert not unmerged_paths(tmp_git_repo)
    assert policy.read_text(encoding="utf-8") == "census: resolved\n"
    assert Repo(tmp_git_repo).git.stash("list") == ""


def test_a_round_that_downgrades_a_guard_is_not_committed_or_landed(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    policy = tmp_git_repo / "docs/ralph-workflow-policy/verification-policy.md"
    policy.parent.mkdir(parents=True)
    row = "| Run every gate | Gate-script owner | a check that cannot run blocks the run | {} |\n"
    policy.write_text(row.format("deterministic: make verify"), encoding="utf-8")
    _commit(tmp_git_repo, "docs/ralph-workflow-policy/verification-policy.md", "track policy")
    head = Repo(tmp_git_repo).head.commit.hexsha
    policy.write_text(row.format("advisory: make verify is slow; review: weekly"), encoding="utf-8")
    integrated: list[object] = []
    monkeypatch.setattr(
        round_landing, "auto_integrate_after_commit", lambda *a, **_k: integrated.append(a)
    )
    scope = WorkspaceScope(root=str(tmp_git_repo), allowed_roots=[str(tmp_git_repo)])

    landing = round_landing.round_landing(conflict_resolver=None, rebase_stop_resolver=None)
    landing(scope, UnifiedConfig(), frozenset())

    assert Repo(tmp_git_repo).head.commit.hexsha == head
    assert integrated == []
