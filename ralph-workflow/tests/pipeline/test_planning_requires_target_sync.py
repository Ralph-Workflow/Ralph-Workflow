"""Planning requires bidirectional synchronization with its integration target.

Dirty completed work routes through normal policy commit phases. An unchanged
integration refusal suspends dispatch and yields to cooldown before retrying.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import run_loop
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState

pytestmark = pytest.mark.timeout_seconds(2.0)

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
                phases={"development": SimpleNamespace(drain="development", role="execution")},
            )
        ),
        active_display=None,
    )


class _Repo:
    """Fake git ground truth: is the target in HEAD, and does it exist."""

    def __init__(self, *, contained: bool | None, target_exists: bool = True) -> None:
        self.contained = contained
        self.landed = True
        self.target_exists = target_exists
        self.integrations = 0


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> _Repo:
    fake = _Repo(contained=True)
    monkeypatch.setattr(
        "ralph.git.merge.observe_branch_sha",
        lambda _root, _name: ("f" * 40 if fake.target_exists else None, True),
    )
    monkeypatch.setattr(
        "ralph.git.merge_obstructions.ancestry_state",
        lambda _root, ancestor, _desc: fake.landed if ancestor == "HEAD" else fake.contained,
    )
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda _s, _c: None)

    return fake


def _integrate_with(
    monkeypatch: pytest.MonkeyPatch, repo: _Repo, *, lands: bool, reason: str
) -> None:
    def _integrate(_ctx: object, _rebase: object) -> RebaseState:
        repo.integrations += 1
        if lands:
            repo.contained = True
            repo.landed = True
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
    assert after.rebase.last_action == "merged"


def test_feature_commits_must_land_on_target_before_planning(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo.landed = False
    _integrate_with(monkeypatch, repo, lands=True, reason="")

    _after, blocked = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )

    assert blocked is None
    assert repo.integrations == 1


def test_failed_landing_blocks_planning_and_persists_critical_error(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo.landed = False
    _integrate_with(monkeypatch, repo, lands=False, reason="target worktree busy")

    after, blocked = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )

    assert blocked is not None
    assert blocked[2] == 0
    assert "CRITICAL" in (after.last_error or "")
    assert "not landed" in (after.last_error or "")


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


def test_unchanged_obstruction_yields_to_cooldown_without_spending_all_attempts(
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
    assert blocked[2] == 0
    assert repo.integrations == 2
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
