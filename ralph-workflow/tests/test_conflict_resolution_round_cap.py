from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_outcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.status import (
    ResolutionStatusReporter,
    phase_label,
    push_conflict_status_bar,
)
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import RetryLaterOutcome
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from tests._conflict_resolution_phase_parity_seams import _install_seams, _policy_bundle

if TYPE_CHECKING:
    from ralph.display.status_bar import StatusBarModel


class _StatusDisplay:
    def __init__(self) -> None:
        self.status_bar = None
        self.run_started_monotonic = 0.0
        self.models: list[StatusBarModel] = []
        self.lines: list[str] = []

    def update_status_bar(self, model: StatusBarModel) -> None:
        self.models.append(model)

    def emit_warn_line(self, _workspace: str, _channel: str, message: str) -> None:
        self.lines.append(message)


class _CooldownClock(FakeClock):
    def now(self) -> float:
        return self.monotonic()


def test_round_cap_regression_session_cap_cannot_override_configured_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured_cap = 7
    config = UnifiedConfig.model_validate(
        {"general": {}, "conflict_resolution": {"max_rounds_per_stop": configured_cap}}
    )
    clock = _CooldownClock()
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock, cycle_cap=configured_cap + 11)
    )
    session = ResolutionSession(
        max_rounds_per_stop=configured_cap + 11,
        recovery_controller=controller,
    )
    display = _StatusDisplay()
    invoked_rounds: list[int] = []
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(
        monkeypatch,
        surviving_per_round=[("src/alpha.py",)] * (configured_cap + 2),
    )

    token = install_executors(clock=clock)
    try:
        outcome = run_conflict_resolution_outcome(
            root=tmp_path,
            target="main",
            config=config,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=display,
            display_context=None,
            invoke=lambda _agent, _prompt, round_index: invoked_rounds.append(round_index) is None,
            session=session,
            prompt_materializer=lambda **_kwargs: tmp_path / "resolution-prompt.md",
        )
    finally:
        reset_executors(token)

    assert isinstance(outcome, RetryLaterOutcome)
    assert invoked_rounds
    assert max(invoked_rounds) <= configured_cap
    assert invoked_rounds == list(range(1, len(invoked_rounds) + 1))
    assert session.max_rounds_per_stop == configured_cap + 11
    assert session.rounds_spent <= configured_cap
    rendered_rounds = [
        (model.outer_dev_iteration, model.outer_dev_cap)
        for model in display.models
        if model.outer_dev_iteration is not None and model.outer_dev_cap is not None
    ]
    assert rendered_rounds
    assert all(round_cap == configured_cap for _, round_cap in rendered_rounds)
    assert all(round_index <= round_cap for round_index, round_cap in rendered_rounds)


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_round_cap_regression_unresolved_success_never_exceeds_cap": (
        "unresolved attempts fall over to the next candidate within each round, "
        "so invoked_rounds records each candidate's invocation per round up to the round cap"
    ),
}


@pytest.mark.parametrize("candidates", [("one",), ("one", "two")])
def test_round_cap_regression_unresolved_success_never_exceeds_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candidates: tuple[str, ...],
) -> None:
    cap = 2
    config = UnifiedConfig.model_validate(
        {"general": {}, "conflict_resolution": {"max_rounds_per_stop": cap}}
    )
    clock = _CooldownClock()
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock, cycle_cap=cap))
    session = ResolutionSession(max_rounds_per_stop=cap, recovery_controller=controller)
    display = _StatusDisplay()
    invoked_rounds: list[int] = []
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: candidates)
    _install_seams(
        monkeypatch,
        surviving_per_round=[("src/alpha.py",)] * (cap + 2),
    )

    token = install_executors(clock=clock)
    try:
        outcome = run_conflict_resolution_outcome(
            root=tmp_path,
            target="main",
            config=config,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=display,
            display_context=None,
            invoke=lambda _agent, _prompt, round_index: invoked_rounds.append(round_index) is None,
            session=session,
            prompt_materializer=lambda **_kwargs: tmp_path / "resolution-prompt.md",
        )
    finally:
        reset_executors(token)

    assert isinstance(outcome, RetryLaterOutcome)
    assert invoked_rounds
    assert invoked_rounds == [r for r in range(1, cap + 1) for _ in candidates]
    assert max(invoked_rounds) <= cap
    assert session.rounds_spent <= cap
    round_models = [model for model in display.models if model.outer_dev_iteration is not None]
    assert round_models
    rendered_rounds = [
        (model.outer_dev_iteration, model.outer_dev_cap)
        for model in round_models
        if model.outer_dev_iteration is not None and model.outer_dev_cap is not None
    ]
    assert rendered_rounds
    assert all(round_index <= round_cap for round_index, round_cap in rendered_rounds)


def test_phase_label_never_renders_round_index_above_cap() -> None:
    cap = 3
    label = phase_label(
        round_index=cap + 1,
        round_cap=cap,
        stop_index=1,
        stop_cap=cap,
    )
    assert f"round {cap + 1}/{cap}" not in label
    assert f"round {cap}/{cap}" in label


def test_phase_label_never_renders_replay_or_stop_numerators_above_caps() -> None:
    cap = 3
    label = phase_label(
        round_index=cap + 1,
        round_cap=cap,
        stop_index=cap + 2,
        stop_cap=cap,
        replay_index=cap + 5,
        replay_total=cap,
    )
    assert f"commit {cap + 5}/{cap}" not in label
    assert f"commit {cap}/{cap}" in label
    assert f"round {cap + 1}/{cap}" not in label
    assert f"round {cap}/{cap}" in label


def test_phase_label_none_handling_unchanged_when_indices_within_cap() -> None:
    cap = 3
    base = phase_label(round_index=2, round_cap=cap, stop_index=None, stop_cap=None)
    assert base == "Rebase Conflict Resolution"
    stop = phase_label(round_index=2, round_cap=cap, stop_index=1, stop_cap=cap)
    assert stop == f"Rebase Conflict Resolution (commit 1/{cap}, round 2/{cap})"
    replay = phase_label(
        round_index=2,
        round_cap=cap,
        stop_index=1,
        stop_cap=cap,
        replay_index=2,
        replay_total=cap,
    )
    assert replay == f"Rebase Conflict Resolution (commit 2/{cap}, round 2/{cap})"


def test_push_conflict_status_bar_never_renders_round_index_above_cap() -> None:
    cap = 3
    display = _StatusDisplay()

    push_conflict_status_bar(
        display,
        Path("/tmp/ws"),
        target="main",
        round_index=cap + 1,
        round_cap=cap,
        stop_index=cap + 2,
        stop_cap=cap,
    )

    assert display.models
    for model in display.models:
        assert f"round {cap + 1}/{cap}" not in model.phase_label
        assert f"round {cap}/{cap}" in model.phase_label
        assert f"commit {cap + 2}/{cap}" not in model.phase_label
        assert f"commit {cap}/{cap}" in model.phase_label


def test_reporter_observe_never_emits_round_index_above_cap() -> None:
    cap = 3
    display = _StatusDisplay()

    class _Event:
        diagnostic: dict[str, object]

        def __init__(self) -> None:
            self.diagnostic = {
                "last_activity_kind": "healthy",
                "last_activity_age_seconds": 1.0,
            }

    reporter = ResolutionStatusReporter(
        display=display,
        target="main",
        round_index=cap + 1,
        round_cap=cap,
        stop_index=1,
        stop_cap=cap,
        clock=lambda: 100.0,
        interval_seconds=30.0,
        started_at=0.0,
        unresolved_paths=("src/alpha.py",),
        agent_name="one",
    )
    reporter.observe(_Event())

    assert display.lines
    for line in display.lines:
        assert f"round={cap + 1}/{cap}" not in line
        assert f"round={cap}/{cap}" in line
