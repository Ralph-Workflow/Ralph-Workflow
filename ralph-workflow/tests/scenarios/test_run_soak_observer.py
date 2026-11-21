from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.state import PipelineState
from tests.scenarios._run_soak import AgentCall, RunObserver, SoakRun, SoakViolation

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]


def test_the_soak_observer_stops_a_run_that_repeats_one_state_without_an_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(tmp_path, monkeypatch)
    soak.seed()
    observer = RunObserver(soak)
    held = PipelineState(phase="planning", last_error="integration has not landed")

    with pytest.raises(SoakViolation, match="repeated one state 6 times"):
        for _step in range(10):
            observer.notify(held)

    assert observer.steps == 6


def test_the_soak_observer_stops_a_run_that_makes_no_progress_between_agent_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak = SoakRun(tmp_path, monkeypatch)
    soak.seed()
    observer = RunObserver(soak)
    review = PipelineState(phase="development_commit_review")

    with pytest.raises(SoakViolation, match="no phase, cycle, commit or landing progress"):
        for nth in range(1, 100):
            observer.agent(AgentCall("development_commit_review", nth, ""))
            observer.notify(review)

    assert observer.steps == 42
