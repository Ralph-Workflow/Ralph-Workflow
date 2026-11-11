from __future__ import annotations

import importlib
import inspect
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.recovery import AgentFailureLedger, AttemptFact, RecoveryController

if TYPE_CHECKING:
    from collections.abc import Callable

_PHASE = "development"
_FAILED_AGENT = "claude"
_OTHER_AGENT = "codex"


@runtime_checkable
class _DirectMcpRecoveryModule(Protocol):
    def run_with_direct_mcp_recovery(
        self,
        run_attempt: Callable[[str | None, Callable[[str], None]], str],
        *,
        max_retries: int,
        reset_tool_registry: Callable[[], object] | None = None,
        on_retry_failure: Callable[[list[str]], object] | None = None,
        on_session_observed: Callable[[str], object] | None = None,
        retry_resumable_exit: bool = False,
    ) -> str: ...


def _recovery_module() -> _DirectMcpRecoveryModule:

    module = importlib.import_module("ralph.agents.invoke._direct_mcp_recovery")
    if isinstance(module, _DirectMcpRecoveryModule):
        return module
    raise AssertionError("the direct-MCP recovery module lost its public entry point")


def _always_failing_attempt(
    failure: Exception,
    calls: list[str | None],
) -> Callable[[str | None, Callable[[str], None]], str]:

    def _attempt(session_id: str | None, _capture: Callable[[str], None]) -> str:
        calls.append(session_id)
        raise failure

    return _attempt


def _empty_response_failure() -> AgentInvocationError:

    return AgentInvocationError(
        "claude",
        1,
        "Model returned an empty response with no tool calls",
    )


def test_retry_cap_exhaustion_preserves_the_original_failure_type() -> None:
    ledger = AgentFailureLedger(clock=FakeClock())
    controller = RecoveryController(ledger=ledger)
    failure = _empty_response_failure()
    calls: list[str | None] = []

    with pytest.raises(AgentInvocationError) as exc_info:
        _recovery_module().run_with_direct_mcp_recovery(
            _always_failing_attempt(failure, calls),
            max_retries=0,
            reset_tool_registry=lambda: None,
        )

    assert type(exc_info.value) is AgentInvocationError
    assert exc_info.value is failure
    assert len(calls) == 1
    assert len(failure.parsed_output) == 1
    assert "retry budget exhausted" in failure.parsed_output[0]
    assert ledger.status(_PHASE, _FAILED_AGENT).is_eligible is True
    assert controller.ledger is ledger
    assert controller.ledger.status(_PHASE, _FAILED_AGENT).is_eligible is True


def test_retry_cap_repeats_the_transport_without_charging_the_ledger() -> None:
    ledger = AgentFailureLedger(clock=FakeClock())
    controller = RecoveryController(ledger=ledger)
    failure = _empty_response_failure()
    calls: list[str | None] = []

    with pytest.raises(AgentInvocationError) as exc_info:
        _recovery_module().run_with_direct_mcp_recovery(
            _always_failing_attempt(failure, calls),
            max_retries=2,
            reset_tool_registry=lambda: None,
        )

    assert exc_info.value is failure
    assert len(calls) == 3
    assert controller.ledger.status(_PHASE, _FAILED_AGENT).is_eligible is True
    assert ledger.status(_PHASE, _OTHER_AGENT).is_eligible is True


def test_only_the_caller_can_change_an_agent_standing() -> None:
    ledger = AgentFailureLedger(clock=FakeClock())
    controller = RecoveryController(ledger=ledger)
    failure = _empty_response_failure()
    calls: list[str | None] = []

    with pytest.raises(AgentInvocationError):
        _recovery_module().run_with_direct_mcp_recovery(
            _always_failing_attempt(failure, calls),
            max_retries=2,
            reset_tool_registry=lambda: None,
        )

    outcome = ledger.record(
        AttemptFact(
            phase=_PHASE,
            agent=_FAILED_AGENT,
            artifact_valid=False,
            workspace_changed=False,
        )
    )
    assert outcome.status.is_eligible is True
    for _attempt in range(2):
        controller.record_attempt_fact(
            _PHASE,
            _FAILED_AGENT,
            artifact_valid=False,
            workspace_changed=False,
        )

    assert ledger.status(_PHASE, _FAILED_AGENT).is_eligible is False
    assert ledger.status(_PHASE, _FAILED_AGENT).is_cooldown is True
    assert ledger.status(_PHASE, _OTHER_AGENT).is_eligible is True


def test_retry_cap_exposes_no_agent_health_seam() -> None:
    parameters = inspect.signature(_recovery_module().run_with_direct_mcp_recovery).parameters
    health_parameters = [
        name
        for name in parameters
        if any(
            word in name.lower()
            for word in (
                "ledger",
                "controller",
                "demot",
                "cooldown",
                "health",
                "eligib",
                "spent",
                "attempt_fact",
            )
        )
    ]

    assert health_parameters == []
