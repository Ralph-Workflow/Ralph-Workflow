from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import cli_integration
from ralph.project_policy.cli_integration import LoadResult, make_production_invoke_agent
from ralph.project_policy.pipeline_graph import PHASE_REMEDIATION
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.project_policy.analysis import InvokePolicyAgent


class _Controller:
    def __init__(self) -> None:
        self.selection_calls: list[tuple[str, ...]] = []

    def preferred_agent_index(self, phase: str, agents: list[str]) -> object:
        del phase
        self.selection_calls.append(tuple(agents))
        selected = agents[min(len(self.selection_calls) - 1, len(agents) - 1)]
        return SimpleNamespace(agent=selected)


def _make_invoke_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    controller: object,
    run_attempt: Callable[..., object],
    run_id: str,
) -> InvokePolicyAgent:
    monkeypatch.setattr(
        cli_integration,
        "build_entry_point_recovery_controller",
        lambda *args, **kwargs: controller,
    )
    monkeypatch.setattr(cli_integration, "_resolve_chain_agents", lambda *args: ["first", "second"])
    monkeypatch.setattr(cli_integration, "run_agent_attempt", run_attempt)
    scope = WorkspaceScope(tmp_path)
    load_result = cli_integration.LoadResult(
        config=UnifiedConfig(),
        workspace_scope=scope,
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=load_policy(default_dir()),
        run_id=run_id,
    )
    display_context = make_display_context()
    return make_production_invoke_agent(
        load_result,
        PipelineDeps(display_context=display_context),
        scope,
        None,
        display_context,
    )


def test_wu_c2_project_policy_regression_fallback_is_controller_selected_without_local_skip_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller = _Controller()
    launched: list[str] = []

    def run_attempt(*args: object, **kwargs: object) -> object:
        del args
        effect = kwargs["effect"]
        assert isinstance(effect, InvokeAgentEffect)
        launched.append(effect.agent_name)
        return SimpleNamespace(
            outcome=SimpleNamespace(
                kind=(
                    AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE
                    if len(launched) == 1
                    else AttemptOutcomeKind.COMPLETED
                )
            )
        )

    invoke = _make_invoke_agent(monkeypatch, tmp_path, controller, run_attempt, "wu-c2")

    assert invoke(phase=PHASE_REMEDIATION, prompt_path=str(tmp_path / "PROMPT.md")) is False
    assert invoke(phase=PHASE_REMEDIATION, prompt_path=str(tmp_path / "PROMPT.md")) is True
    assert launched == ["first", "second"]
    assert controller.selection_calls == [("first", "second"), ("first", "second")]


class _WaitClock:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class _CoolingController:
    def __init__(self) -> None:
        self.clock = _WaitClock()
        self.selection_calls: list[tuple[str, ...]] = []
        self.wait_calls: list[tuple[str, tuple[str, ...]]] = []
        self.prune_calls = 0

    def preferred_agent_index(self, phase: str, agents: list[str]) -> object:
        del phase
        self.selection_calls.append(tuple(agents))
        agent = None if len(self.selection_calls) == 1 else agents[1]
        return SimpleNamespace(agent=agent)

    def earliest_available_wait_ms(self, phase: str, agents: list[str]) -> int:
        self.wait_calls.append((phase, tuple(agents)))
        return 250

    def prune_expired(self) -> None:
        self.prune_calls += 1


def test_wu_c2_project_policy_wait_regression_all_cooled_uses_shared_wait_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller = _CoolingController()
    launched: list[str] = []

    def run_attempt(*args: object, **kwargs: object) -> object:
        del args
        effect = kwargs["effect"]
        assert isinstance(effect, InvokeAgentEffect)
        launched.append(effect.agent_name)
        return SimpleNamespace(outcome=SimpleNamespace(kind=AttemptOutcomeKind.COMPLETED))

    invoke = _make_invoke_agent(monkeypatch, tmp_path, controller, run_attempt, "wu-c2-wait")

    assert invoke(phase=PHASE_REMEDIATION, prompt_path=str(tmp_path / "PROMPT.md")) is True
    assert controller.wait_calls == [(PHASE_REMEDIATION, ("first", "second"))]
    assert controller.clock.sleeps == [0.25]
    assert controller.prune_calls == 1
    assert controller.selection_calls == [("first", "second"), ("first", "second")]
    assert launched == ["second"]
