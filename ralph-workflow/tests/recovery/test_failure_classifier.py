"""Cursor authentication failures are user configuration errors."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.agents.invoke import BrokenAgentExitError
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
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
    controller = RecoveryController(
        options=RecoveryControllerOptions(policy_bundle=policy_bundle)
    )
    def fail_if_stale_session_handling_runs(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cursor authentication must not enter stale-session handling")

    monkeypatch.setattr(controller, "_write_session_reset_hint", fail_if_stale_session_handling_runs)
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
