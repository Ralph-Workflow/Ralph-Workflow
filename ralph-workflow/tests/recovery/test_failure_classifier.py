"""Cursor authentication failures are user configuration errors."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.agents.invoke import BrokenAgentExitError
from ralph.mcp.server.lifecycle import McpServerError
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent, FailureEventBus
from ralph.recovery.failure_classifier import FailureCategory, FailureClassifier


@pytest.mark.parametrize(
    "message",
    (
        "Cursor authentication required",
        "Please run 'agent login' before continuing",
        "CURSOR_API_KEY is missing",
    ),
)
def test_cursor_auth_message_only_is_user_config(message: str) -> None:
    failure = FailureClassifier().classify(
        message,
        phase="development",
        agent="cursor",
    )

    assert failure.category == FailureCategory.USER_CONFIG
    assert failure.counts_against_budget is False
    assert failure.reset_session is False


@pytest.mark.parametrize(
    "stderr",
    (
        "Cursor authentication required",
        "Please run 'agent login' before continuing",
        "CURSOR_API_KEY is missing",
    ),
)
def test_cursor_auth_broken_agent_exit_is_user_config(stderr: str) -> None:
    failure = FailureClassifier().classify(
        BrokenAgentExitError("cursor", reason="no_output", stderr=stderr),
        phase="development",
        agent="cursor",
    )

    assert failure.category == FailureCategory.USER_CONFIG
    assert failure.counts_against_budget is False
    assert failure.reset_session is False


def test_cursor_auth_incident_fails_as_user_config_without_stale_session_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        policy_bundle = load_policy(Path(directory) / ".agent")
    controller = RecoveryController(options=RecoveryControllerOptions(policy_bundle=policy_bundle))

    def fail_if_stale_session_handling_runs(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cursor authentication must not enter stale-session handling")

    monkeypatch.setattr(
        controller, "_write_session_reset_hint", fail_if_stale_session_handling_runs
    )
    state = PipelineState(
        phase="development_analysis",
        phase_chains={
            "development_analysis": AgentChainState(
                agents=["cursor/auto"], current_index=0, retries=0
            )
        },
        last_agent_session_id="valid-cursor-session",
    )
    error = BrokenAgentExitError(
        "cursor/auto",
        reason="no_output",
        stderr=(
            "Error: Authentication required. Please run 'agent login' first, "
            "or set CURSOR_API_KEY environment variable."
        ),
    )

    new_state, effects, event = controller.handle(
        state,
        error,
        FailureContext(phase="development_analysis", agent="cursor/auto"),
    )

    assert event.category == str(FailureCategory.USER_CONFIG)
    assert event.counted_against_budget is False
    assert new_state.last_failure_category == str(FailureCategory.USER_CONFIG)
    assert new_state.phase == "failed_terminal"
    assert effects == []


def test_generic_broken_agent_exit_remains_agent_failure() -> None:
    failure = FailureClassifier().classify(
        BrokenAgentExitError("cursor", reason="no_output", stderr="process exited"),
        phase="development",
        agent="cursor",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is True


def test_mcp_server_error_classified_as_environmental() -> None:
    """McpServerError is classified as ENVIRONMENTAL, not charged to the agent."""
    failure = FailureClassifier().classify(
        McpServerError("MCP server crashed", restart_count=0),
        phase="development",
        agent="dev",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False
    assert failure.reset_session is False


def test_mcp_server_error_preserves_chain_cap_in_recovery_controller() -> None:
    """Bug 1 AC 5: a McpServerError does NOT debit the agent chain cap.

    ``RecoveryController.handle`` runs the typed McpServerError through
    the failure classifier, which routes it as
    ``(FailureCategory.ENVIRONMENTAL, False, False)``. The
    ``with_retry_increment`` path in
    ``recovery/controller.py`` is gated on
    ``failure.counts_against_budget``; an environmental failure leaves
    ``chain.current_index`` untouched, and the controller emits a
    ``FailureEvent(counted_against_budget=False,
    chain_capacity_remaining=N)``.

    The technical-retry path at ``controller.py:705-721, 1370-1378``
    may still increment the in-session ``retries`` counter on
    ``chain_for_phase(...)``; that is a separate counter and is
    allowed to increment. This test pins the chain cap
    (``chain.current_index``) — the agent-budget counter that gates
    agent selection across phases — which is what must NOT decrement
    for an environmental failure.
    """
    bus = FailureEventBus()
    collected: list[FailureEvent] = []
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10, event_bus=bus))
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude", "codex"], current_index=0, retries=0)
        },
    )

    new_state, _effects, evt = controller.handle(
        state,
        McpServerError("MCP server process exited before endpoint became ready", restart_count=0),
        FailureContext(phase="development", agent="claude"),
    )

    # The FailureEvent reports uncounted (no chain cap debit).
    assert evt.category == str(FailureCategory.ENVIRONMENTAL)
    assert evt.counted_against_budget is False
    assert any(e is evt for e in collected), (
        "FailureEvent must be published on the bus so the run-loop can log counted=False"
    )

    # The chain cap is preserved: the same agent (claude at index 0)
    # remains the current selection across the failure. The next
    # attempt re-invokes claude, NOT codex.
    new_chain = new_state.phase_chains["development"]
    assert new_chain.current_index == 0, (
        "Chain cap (current_index) must be preserved for an environmental "
        "failure; an environmental failure must not advance the agent selection"
    )
