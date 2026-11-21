from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.scenarios._run_soak import Interrupt, SoakRun, assert_soak_invariant, git

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(30),
]


def test_a_run_interrupted_at_every_phase_boundary_resumes_each_time_and_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(tmp_path, monkeypatch)
    soak.seed()

    outcome = soak.interrupt_and_resume(
        Interrupt("agent", "planning"),
        Interrupt("agent", "planning_analysis"),
        Interrupt("agent", "development"),
        Interrupt("agent", "development_commit_review"),
        Interrupt("agent", "development_commit"),
        Interrupt("step", "development_final_commit_review"),
        Interrupt("step", "development_final_commit"),
        Interrupt("step", "complete"),
    )

    assert_soak_invariant(soak, outcome)
    assert git(soak.root, "rev-parse", "HEAD") == soak.target_sha()
    assert git(soak.root, "log", "-1", "--format=%s") == "feat: soak change 1"
