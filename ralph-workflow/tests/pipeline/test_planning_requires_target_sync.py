"""Planning never starts on a branch behind its integration target.

The gate reads ground truth (``merge-base --is-ancestor <target> HEAD``),
never an integration record: observed 2026-10-05, a refused endpoint merge
left a clean tree, the record-based gate called that "resolved", and
planning ran 40 commits behind ``main``. When the branch is behind, the
gate itself integrates -- preserving uncommitted work first, then running
the integration WITH the conflict resolver -- and retries up to the
resolver budget. Nobody else is there to do it. Only a spent budget stops
the run, and it never plans behind.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate_remote_sync import MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from pathlib import Path


def _ctx(tmp_path: Path, *, enabled: bool = True, target: str = "main") -> SimpleNamespace:
    (tmp_path / ".git").mkdir(exist_ok=True)
    return SimpleNamespace(
        config=SimpleNamespace(
            general=SimpleNamespace(auto_integrate_enabled=enabled, auto_integrate_target=target)
        ),
        workspace_scope=SimpleNamespace(root=tmp_path),
        policy_bundle=SimpleNamespace(
            pipeline=SimpleNamespace(
                entry_phase="planning",
                phases={"development": SimpleNamespace(drain="development")},
            )
        ),
        active_display=None,
    )


class _Repo:
    """Fake git ground truth: is the target in HEAD, and does it exist."""

    def __init__(self, *, contained: bool | None, target_exists: bool = True) -> None:
        self.contained = contained
        self.target_exists = target_exists
        self.integrations = 0
        self.preserved = 0


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> _Repo:
    fake = _Repo(contained=True)
    monkeypatch.setattr(
        "ralph.git.merge.observe_branch_sha",
        lambda _root, _name: ("f" * 40 if fake.target_exists else None, True),
    )
    monkeypatch.setattr(
        "ralph.git.merge_obstructions.ancestry_state", lambda _root, _anc, _desc: fake.contained
    )
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda _s, _c: None)

    def _preserve(_root: object, _target: object) -> None:
        fake.preserved += 1

    monkeypatch.setattr(run_loop, "_preserve_uncommitted_work", _preserve)
    return fake


def _integrate_with(
    monkeypatch: pytest.MonkeyPatch, repo: _Repo, *, lands: bool, reason: str
) -> None:
    def _integrate(_ctx: object, _rebase: object) -> RebaseState:
        repo.integrations += 1
        if lands:
            repo.contained = True
            return RebaseState(last_action="merged", last_target="main")
        return RebaseState(last_action="conflict", last_reason=reason, last_target="main")

    monkeypatch.setattr(run_loop, "_run_startup_integration", _integrate)


def test_in_sync_branch_plans_without_integrating(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _integrate_with(monkeypatch, repo, lands=True, reason="")
    state = PipelineState(phase="planning")

    after, blocked = run_loop._ensure_planning_in_sync(state, _ctx(tmp_path), "planning")

    assert blocked is None
    assert after is state
    assert repo.integrations == 0


def test_behind_branch_is_integrated_before_planning(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo.contained = False
    _integrate_with(monkeypatch, repo, lands=True, reason="")

    after, blocked = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )

    assert blocked is None
    assert repo.integrations == 1
    assert repo.preserved == 1, "uncommitted work is preserved before integrating"
    assert after.rebase.last_action == "merged"


def test_integration_is_retried_until_it_lands(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo.contained = False

    def _integrate(_ctx: object, _rebase: object) -> RebaseState:
        repo.integrations += 1
        if repo.integrations == 2:
            repo.contained = True
            return RebaseState(last_action="merged", last_target="main")
        return RebaseState(last_action="conflict", last_reason="resolver did not finish")

    monkeypatch.setattr(run_loop, "_run_startup_integration", _integrate)

    _after, blocked = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )

    assert blocked is None
    assert repo.integrations == 2


def test_behind_branch_stops_only_after_the_resolver_budget_is_spent(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo.contained = False
    _integrate_with(
        monkeypatch,
        repo,
        lands=False,
        reason="endpoint merge refused: untracked files would be overwritten: a.txt",
    )

    after, blocked = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )

    assert blocked is not None
    assert blocked[2] == 1
    assert repo.integrations == MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
    assert "does not contain the tip of 'main'" in (after.last_error or "")
    assert "untracked files would be overwritten: a.txt" in (after.last_error or "")


def test_unprovable_ancestry_is_never_treated_as_in_sync(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo.contained = None
    _integrate_with(monkeypatch, repo, lands=False, reason="ancestry unreadable")

    _after, blocked = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )

    assert blocked is not None


def test_missing_target_branch_stops_with_the_setting_to_fix(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo.target_exists = False
    _integrate_with(monkeypatch, repo, lands=False, reason="missing target")

    after, blocked = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )

    assert blocked is not None
    assert "general.auto_integrate_target" in (after.last_error or "")


def test_non_planning_phase_and_disabled_integration_are_not_gated(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo.contained = False
    _integrate_with(monkeypatch, repo, lands=False, reason="")

    _a, blocked_dev = run_loop._ensure_planning_in_sync(
        PipelineState(phase="development"), _ctx(tmp_path), "planning"
    )
    _b, blocked_disabled = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path, enabled=False), "planning"
    )

    assert blocked_dev is None
    assert blocked_disabled is None
    assert repo.integrations == 0
