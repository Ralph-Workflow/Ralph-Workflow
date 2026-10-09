"""Regression contracts for conflict-resolution relay infrastructure failure (S-4)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.idle_watchdog.timeout_policy import TimeoutProfile
from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    SupervisionInfrastructureError,
    raise_on_relay_health_error,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.session import (
    RALPH_FAULT_ESCALATION_HITS,
    ResolutionSession,
    invoke_resolution_agent,
    wrap_activity_listener,
)
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.scope import WorkspaceScope
from tests._conflict_resolution_phase_parity_seams import _policy_bundle
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._session_fake_mcp_bridge import _FakeMcpBridge
from tests._session_registry_instance import _RegistryInstance

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.pipeline.events import PipelineEvent


def test_conflict_resolution_relay_failure_precedes_inactivity_classification() -> None:
    """S-4: a relay fault is typed infrastructure failure, never conflict inactivity."""
    reader = type(
        "Reader", (), {"_relay_health_error": lambda self: "relay acknowledgement timed out"}
    )()

    with pytest.raises(
        SupervisionInfrastructureError, match="SUPERVISION_INFRASTRUCTURE_FAILURE"
    ) as exc_info:
        raise_on_relay_health_error(reader, "resolver")

    assert "CONFLICT_INACTIVITY" not in str(exc_info.value)
    assert exc_info.value.detail == "relay acknowledgement timed out"


@pytest.mark.parametrize(
    "reason",
    [
        ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
        ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED,
    ],
)
def test_confirmed_relay_error_interrupts_resolution_despite_fresh_subagent_output(
    monkeypatch: pytest.MonkeyPatch,
    reason: ResolutionTerminationReason,
) -> None:
    session = ResolutionSession()
    output = f"{reason.value}: activity relay sender: timed out"

    def execute(effect: InvokeAgentEffect, *_args: object, **_kwargs: object) -> PipelineEvent:
        assert effect.activity_status_listener is not None
        health = effect.supervision_health_error
        assert health is not None
        clock = FakeClock()
        watchdog = IdleWatchdog(
            TimeoutPolicy(
                profile=TimeoutProfile.ACTIVITY_ONLY,
                idle_timeout_seconds=900.0,
                activity_only_status_interval_seconds=1.0,
            ),
            clock,
            listener=effect.activity_status_listener,
        )
        watchdog.record_invocation_start()
        for _ in range(RALPH_FAULT_ESCALATION_HITS):
            assert health() is None
            clock.advance(1.0)
            watchdog.record_subagent_work(description=output)
            watchdog.evaluate(lambda: AgentExecutionState.WAITING_ON_CHILD)
        detail = health()
        assert detail is not None
        raise SupervisionInfrastructureError("resolver", detail)

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        execute,
    )
    assert (
        invoke_resolution_agent(
            agent_name="resolver",
            prompt_path=Path("/workspace/prompt.md"),
            config=UnifiedConfig.model_validate({"general": {}}),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            session=session,
        )
        is False
    )
    assert session.terminal_reason is reason
    assert isinstance(session.last_attempt_failure, SupervisionInfrastructureError)
    assert session.charge_conflict_budget is False


def test_one_quoted_fault_does_not_escalate_on_repeated_watchdog_ticks() -> None:
    session = ResolutionSession()
    faults: list[ResolutionTerminationReason] = []
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            profile=TimeoutProfile.ACTIVITY_ONLY,
            idle_timeout_seconds=900.0,
            activity_only_status_interval_seconds=1.0,
        ),
        clock,
        listener=wrap_activity_listener(
            None, session, agent_name="resolver", on_fault=faults.append
        ),
    )
    watchdog.record_invocation_start()
    watchdog.record_subagent_work(
        description="read source: SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender"
    )
    for _ in range(RALPH_FAULT_ESCALATION_HITS + 1):
        clock.advance(1.0)
        watchdog.evaluate(lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert faults == []
    assert session.stop_dead_surfaces == ()


def test_silent_infrastructure_failure_does_not_charge_conflict_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ResolutionSession()

    def execute(*_args: object, **_kwargs: object) -> PipelineEvent:
        raise SupervisionInfrastructureError("resolver", "activity relay sender: timed out")

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        execute,
    )
    assert (
        invoke_resolution_agent(
            agent_name="resolver",
            prompt_path=Path("/workspace/prompt.md"),
            config=UnifiedConfig.model_validate({"general": {}}),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            session=session,
        )
        is False
    )
    assert session.charge_conflict_budget is False
    assert session.terminal_reason is ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE


def test_executor_delivers_resolution_health_to_the_invocation(tmp_path: Path) -> None:
    agent = AgentConfig(cmd="resolver")
    display = make_display_context()
    config = UnifiedConfig.model_validate({"general": {"max_retries": 0}})
    deps = make_test_pipeline_deps(
        display,
        bridge=_FakeMcpBridge(),
        registry_factory=lambda _config: _RegistryInstance(agent),
        master_prompt_materializer=lambda workspace_root, name, **_kwargs: str(
            workspace_root / f"{name}.md"
        ),
    )
    fault: list[str] = []
    observed: list[str | None] = []

    def invoke(
        _config: AgentConfig, _prompt: str, *, options: InvokeOptions | None = None
    ) -> Iterator[object]:
        assert options is not None
        health = options.relay_health_error
        assert health is not None
        observed.append(health())
        fault.append("SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender: timed out")
        observed.append(health())
        raise SupervisionInfrastructureError("resolver", fault[0])

    with pytest.raises(SupervisionInfrastructureError, match="timed out"):
        execute_agent_effect(
            InvokeAgentEffect(
                agent_name="resolver",
                phase="rebase_conflict_resolution",
                prompt_file=str(tmp_path / "prompt.md"),
                activity_only_supervision=True,
                supervision_health_error=lambda: fault[0] if fault else None,
            ),
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=display,
            policy_bundle=_policy_bundle(),
            invoke_agent=invoke,
            agent_invocation_error=AgentInvocationError,
        )
    assert observed == [None, fault[0]]


def test_fault_latched_while_consuming_output_precedes_completion() -> None:
    from ralph.agents.invoke import ProcessReaderCtx, read_lines_from_process
    from tests.agents.invoke.test_reader_stall_lifetime import _FakeManagedProcess

    faults: list[str] = []
    complete: list[bool] = []
    lines = read_lines_from_process(
        _FakeManagedProcess(),
        ctx=ProcessReaderCtx(
            config=AgentConfig(cmd="resolver"),
            policy=TimeoutPolicy(
                profile=TimeoutProfile.ACTIVITY_ONLY,
                idle_timeout_seconds=900.0,
                process_monitor_enabled=False,
            ),
            completion_is_terminal=lambda: bool(complete),
            relay_health_error=lambda: faults[0] if faults else None,
        ),
        _clock=FakeClock(),
    )
    assert next(lines) == "completed output\n"
    faults.append("activity relay sender: timed out")
    complete.append(True)
    with pytest.raises(SupervisionInfrastructureError, match="timed out"):
        list(lines)
