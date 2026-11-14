from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_outcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import RetryLaterOutcome
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
    session = ResolutionSession(max_rounds_per_stop=cap)
    display = _StatusDisplay()
    invoked_rounds: list[int] = []
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: candidates)
    _install_seams(
        monkeypatch,
        surviving_per_round=[("src/alpha.py",)] * (cap + 2),
    )

    token = install_executors(clock=_CooldownClock())
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
    assert invoked_rounds == list(range(1, len(invoked_rounds) + 1))
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
