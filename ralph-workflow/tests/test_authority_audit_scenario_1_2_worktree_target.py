from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.pipeline._integration_action import (
    IntegrationAction,
    next_integration_action,
)
from ralph.pipeline._pipeline_state_machine import (
    PhaseDecision,
    decide_phase_transition,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    assert _git(repo, "init", "-b", "main").returncode == 0
    assert _git(repo, "config", "user.name", "Test").returncode == 0
    assert _git(repo, "config", "user.email", "test@example.com").returncode == 0
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    assert _git(repo, "add", "shared.txt").returncode == 0
    assert _git(repo, "commit", "-m", "init").returncode == 0
    return repo


def _add_worktree(parent: Path, name: str, branch: str) -> Path:
    worktree = parent.parent / f"{parent.name}-{name}"
    assert _git(parent, "worktree", "add", str(worktree), "-b", branch).returncode == 0
    return worktree


@pytest.mark.subprocess_e2e
def test_linked_worktree_unrelated_branch_does_not_break_retained_obligations(
    tmp_path: Path,
) -> None:
    main = _make_repo(tmp_path, "main")
    _add_worktree(main, "wt", "feature")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="f" * 40,
        pre_target_sha="a" * 40,
    )
    write_record(main, record)

    (main / "shared.txt").write_text("advance\n", encoding="utf-8")
    assert _git(main, "add", "shared.txt").returncode == 0
    assert _git(main, "commit", "-m", "advance").returncode == 0

    loaded = read_record(main)
    assert loaded is not None
    state = PipelineState(phase="development", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        None,
        integration_record=loaded,
        rebase_state=state.rebase,
    )
    assert decision is PhaseDecision.BLOCK_FOR_RECOVERY
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED


@pytest.mark.subprocess_e2e
def test_target_movement_during_integration_keeps_ownership(
    tmp_path: Path,
) -> None:
    main = _make_repo(tmp_path, "main")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="f" * 40,
        pre_target_sha="a" * 40,
        reintegrate_pending=True,
    )
    write_record(main, record)

    loaded = read_record(main)
    assert loaded is not None
    assert loaded.reintegrate_pending is True
    action = next_integration_action(loaded)
    assert action is IntegrationAction.REINTEGRATE_AGAINST_TARGET
    assert loaded.pre_feature_sha == "f" * 40
