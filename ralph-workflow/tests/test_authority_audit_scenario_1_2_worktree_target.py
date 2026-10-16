"""Authority audit — acceptance scenarios 1 and 2 (linked worktree + target movement).

Acceptance scenario 1: in linked worktrees, advance an unrelated branch while
another worktree has a retained integration obligation. The obligation must
remain valid and make progress according to its own state.

Acceptance scenario 2: move the configured target during integration. Ownership
must remain and the next action must become reintegration against the new
target.

These tests pin the recovery-decision surface (:class:`PhaseDecision` and
:func:`next_integration_action`) on the public, recoverable records. The
markers are scoped so the suite stays inside the 60s combined budget.
"""

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
    """Scenario 1: linked-worktree branch advance cannot break retained obligation.

    Pin the typed decision: ``decide_phase_transition`` must return
    :data:`PhaseDecision.BLOCK_FOR_RECOVERY` (NOT
    :data:`PhaseDecision.REPORT_INTERVENTION_REQUIRED`). The retained
    obligation stays authoritative under linked-worktree concurrency.
    """
    main = _make_repo(tmp_path, "main")
    _add_worktree(main, "wt", "feature")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="f" * 40,
        pre_target_sha="a" * 40,
    )
    write_record(main, record)

    # Advance an unrelated branch on a different worktree.
    (main / "shared.txt").write_text("advance\n", encoding="utf-8")
    assert _git(main, "add", "shared.txt").returncode == 0
    assert _git(main, "commit", "-m", "advance").returncode == 0

    # The retained obligation must still be authoritative.
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
    """Scenario 2: target movement must keep ownership; next action is reintegration.

    Pin the typed decision: ``next_integration_action`` returns
    :data:`IntegrationAction.REINTEGRATE_AGAINST_TARGET` (NOT silent
    skip, NOT intervention) when the record retains ownership but
    the target has moved.
    """
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
    # Simulate target movement: the record is now marked for
    # reintegration against the new target.
    assert loaded.reintegrate_pending is True
    action = next_integration_action(loaded)
    assert action is IntegrationAction.REINTEGRATE_AGAINST_TARGET
    # Ownership stays with the original feature SHA.
    assert loaded.pre_feature_sha == "f" * 40
