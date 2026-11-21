from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke.completion_quiesce import record_partial_child_edits
from tests.scenarios._run_soak import (
    TARGET,
    WORK_FILE,
    AgentCall,
    SoakRun,
    assert_soak_invariant,
    git,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(30),
]


def _edit_the_work_file_on_target(soak: SoakRun, call: AgentCall) -> None:
    soak.move_target(WORK_FILE, f"VALUE = 'target {call.nth}'\n", "target edits the work file")


def test_a_conflicting_target_move_is_resolved_while_the_catch_up_worker_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(
        tmp_path,
        monkeypatch,
        catchup_interval=1.0,
        hooks={("development", 1): _edit_the_work_file_on_target},
    )
    soak.seed()

    outcome = soak.run()

    assert_soak_invariant(soak, outcome)
    assert outcome.phases.count("rebase_conflict_resolution") == 1, outcome.describe()
    assert git(soak.root, "rev-parse", "HEAD") == soak.target_sha()
    assert git(soak.primary, "show", f"{TARGET}:{WORK_FILE}") == "resolved by the soak resolver"


def _declare_completion_with_children_working(soak: SoakRun, _call: AgentCall) -> None:
    record_partial_child_edits(soak.root, agent="claude", children=("ses_child",))
    soak.move_target("docs/target.md", "target\n", "target adds a doc")


def _late_child_edit_before_the_review(soak: SoakRun, call: AgentCall) -> None:
    assert "Possibly partial edits" in call.prompt
    (soak.root / "src" / "child.py").write_text("CHILD = 1\n", encoding="utf-8")


def test_child_edits_after_a_declared_completion_land_with_the_run_on_a_moved_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(
        tmp_path,
        monkeypatch,
        hooks={
            ("development", 1): _declare_completion_with_children_working,
            ("development_commit_review", 1): _late_child_edit_before_the_review,
        },
    )
    soak.seed()

    outcome = soak.run()

    assert_soak_invariant(soak, outcome)
    assert outcome.phases.count("development_commit_review") == 2, outcome.describe()
    assert git(soak.root, "rev-parse", "HEAD") == soak.target_sha()
    assert git(soak.primary, "show", f"{TARGET}:src/child.py") == "CHILD = 1"
    assert git(soak.primary, "show", f"{TARGET}:docs/target.md") == "target"
