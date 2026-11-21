from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.scenarios._run_soak import (
    TARGET,
    WORK_FILE,
    AgentCall,
    Interrupt,
    SoakRun,
    assert_soak_invariant,
    git,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(60)]


def _add_a_target_doc(soak: SoakRun, _call: AgentCall) -> None:
    soak.move_target("docs/target.md", "target\n", "target adds a doc")


def test_a_run_forked_before_thousands_of_target_commits_lands_on_the_moved_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(tmp_path, monkeypatch, hooks={("development", 1): _add_a_target_doc})
    soak.seed(history=2000)

    outcome = soak.run()

    assert_soak_invariant(soak, outcome)
    assert git(soak.root, "rev-parse", "HEAD") == soak.target_sha()
    assert int(git(soak.primary, "rev-list", "--count", TARGET)) > 2000


def _edit_the_work_file_on_target(soak: SoakRun, call: AgentCall) -> None:
    soak.move_target(WORK_FILE, f"VALUE = 'target {call.nth}'\n", "target edits the work file")


def test_a_landing_interrupted_mid_conflict_resolution_resumes_onto_a_target_that_moved_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(tmp_path, monkeypatch, hooks={("development", 1): _edit_the_work_file_on_target})
    soak.seed()
    interrupted = soak.run(interrupt_at=Interrupt("agent", "rebase_conflict_resolution"))
    soak.move_target(WORK_FILE, "VALUE = 'target again'\n", "target edits the work file again")

    outcome = soak.resume()

    assert interrupted.exit_code == 130, interrupted.describe()
    assert_soak_invariant(soak, outcome)
    assert git(soak.root, "rev-parse", "HEAD") == soak.target_sha()
    assert git(soak.primary, "show", f"{TARGET}:{WORK_FILE}") == "resolved by the soak resolver"
