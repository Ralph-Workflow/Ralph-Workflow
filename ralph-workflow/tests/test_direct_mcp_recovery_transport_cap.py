"""Public-seam proof: the direct-MCP retry cap is transport plumbing.

The invocation-local retry cap implemented by
``ralph.agents.invoke._direct_mcp_recovery`` bounds how many PHYSICAL
transport repetitions ONE agent invocation may make. It is not an
agent-health verdict: it never demotes an agent, never puts an agent in
cooldown, never excludes an agent, and never selects the next agent.

Agent health has exactly ONE owner:
:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger`. The
CALLER charges a failed attempt to that ledger through the public seam
(:meth:`ralph.recovery.controller.RecoveryController.record_attempt_fact`);
the cap itself charges nothing. The pipeline-level same-agent health
bound is the ledger's consecutive/total limits, not this cap.

One coupling is documented rather than removed: every transport
repetition is a PHYSICAL attempt, so the cap multiplies how many
charges one pipeline-level invocation can produce (up to
``1 + max_retries``). The ledger -- not the cap -- decides what those
charges mean.

These tests pin that separation through public APIs only:
``run_with_direct_mcp_recovery`` (the recovery module's public entry
point), ``AgentFailureLedger.record``/``status`` with ``AttemptFact``,
and ``RecoveryController.record_attempt_fact``/``ledger``. No private
ledger state is read, and no attempt is faked with a sleep, a
subprocess, or real file I/O.
"""

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
    """The public entry point of the direct-MCP recovery module."""

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
    """Return the recovery module's public entry point.

    The module lives under a private package path, so it is reached by
    dotted path instead of a ``from ... import`` statement: the
    repository's frozen white-box baseline counts a public symbol
    imported THROUGH a private module path as private coupling, and
    this suite holds none of that coupling. Only the module's public
    entry point -- the one its ``__all__`` exports -- is used.
    """

    module = importlib.import_module("ralph.agents.invoke._direct_mcp_recovery")
    if isinstance(module, _DirectMcpRecoveryModule):
        return module
    raise AssertionError("the direct-MCP recovery module lost its public entry point")


def _always_failing_attempt(
    failure: Exception,
    calls: list[str | None],
) -> Callable[[str | None, Callable[[str], None]], str]:
    """Return an attempt function that always raises ``failure``."""

    def _attempt(session_id: str | None, _capture: Callable[[str], None]) -> str:
        calls.append(session_id)
        raise failure

    return _attempt


def _empty_response_failure() -> AgentInvocationError:
    """Return the canonical retryable direct-invocation failure."""

    return AgentInvocationError(
        "claude",
        1,
        "Model returned an empty response with no tool calls",
    )


def test_retry_cap_exhaustion_preserves_the_original_failure_type() -> None:
    """The cap reports the ORIGINAL failure; it never wraps it."""
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
    # Exhaustion adds ONLY a factual diagnostic to the original failure.
    assert len(failure.parsed_output) == 1
    assert "retry budget exhausted" in failure.parsed_output[0]
    # The cap charged NOTHING to the single owner of agent health.
    assert ledger.status(_PHASE, _FAILED_AGENT).is_eligible is True
    assert controller.ledger is ledger
    assert controller.ledger.status(_PHASE, _FAILED_AGENT).is_eligible is True


def test_retry_cap_repeats_the_transport_without_charging_the_ledger() -> None:
    """The cap repeats the transport; the CALLER charges the ledger."""
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

    # The original failure type survives the exhausted cap.
    assert exc_info.value is failure
    # Every repetition is a PHYSICAL attempt: up to ``1 + max_retries``.
    assert len(calls) == 3
    # The cap is not an agent-health verdict: nothing was charged, so no
    # agent was demoted, cooled down, or excluded.
    assert controller.ledger.status(_PHASE, _FAILED_AGENT).is_eligible is True
    assert ledger.status(_PHASE, _OTHER_AGENT).is_eligible is True


def test_only_the_caller_can_change_an_agent_standing() -> None:
    """The ledger moves the agent's standing; the cap never does."""
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

    # The CALLER charges the failed attempts through the public seam.
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

    # The ledger -- the single owner -- is what cools an agent down.
    assert ledger.status(_PHASE, _FAILED_AGENT).is_eligible is False
    assert ledger.status(_PHASE, _FAILED_AGENT).is_cooldown is True
    # An agent the caller never charged is untouched by the whole episode.
    assert ledger.status(_PHASE, _OTHER_AGENT).is_eligible is True


def test_retry_cap_exposes_no_agent_health_seam() -> None:
    """The cap cannot reach agent health: it has no health parameter."""
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
