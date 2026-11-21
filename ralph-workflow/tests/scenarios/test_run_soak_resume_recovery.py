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

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(30),
]


def _no_artifact(_soak: SoakRun, _call: AgentCall) -> bool:
    return True


def test_a_run_interrupted_during_a_cooldown_wait_resumes_and_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(
        tmp_path, monkeypatch, hooks={("development", nth): _no_artifact for nth in (1, 2, 3)}
    )
    soak.seed()

    outcome = soak.interrupt_and_resume(Interrupt("sleep", "*"))

    assert_soak_invariant(soak, outcome)
    assert git(soak.root, "rev-parse", "HEAD") == soak.target_sha()


def _edit_the_materialized_prompt_copy(soak: SoakRun, _call: AgentCall) -> None:
    criteria = soak.root / ".agent" / "PRODUCT_CRITERIA.md"
    criteria.write_text(criteria.read_text(encoding="utf-8") + "\nagent note\n", encoding="utf-8")


def test_a_cli_resume_keeps_state_after_a_prompt_copy_edit_and_commits_leftover_staged_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(
        tmp_path, monkeypatch, hooks={("development", 1): _edit_the_materialized_prompt_copy}
    )
    soak.seed()
    resumed = soak.interrupt_and_resume(Interrupt("agent", "development_commit_review"), cli=True)
    assert_soak_invariant(soak, resumed)
    assert "planning" not in resumed.phases, resumed.describe()
    (soak.root / WORK_FILE).write_text("VALUE = 'leftover'\n", encoding="utf-8")
    git(soak.root, "add", "--", WORK_FILE)

    outcome = soak.resume(cli=True)

    assert_soak_invariant(soak, outcome)
    assert git(soak.primary, "show", f"{TARGET}:{WORK_FILE}") == "VALUE = 'leftover'"
