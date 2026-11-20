from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import SupervisionInfrastructureError
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    invoke_resolution_agent,
)
from ralph.pipeline.resolution_outcome import RetryLaterOutcome
from ralph.recovery.controller import RecoveryController
from tests._conflict_resolution_phase_parity_seams import _policy_bundle

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_fault_text_cannot_produce_a_transport_loop_verdict": (
        "the resolver no longer classifies agent message text; a typed supervision fault is "
        "reported as the supervision-infrastructure outcome whatever text it carries, and the "
        "test pins that the transport-loop verdict can no longer be produced from text"
    ),
    "test_the_same_typed_fault_gives_the_same_verdict_for_every_text": (
        "the resolver no longer classifies agent message text; the test pins that the verdict "
        "depends on the typed fault alone and not on the text it carries"
    ),
}


def _invoke_with_fault_detail(monkeypatch: pytest.MonkeyPatch, detail: str) -> ResolutionSession:
    session = ResolutionSession(recovery_controller=RecoveryController())

    def execute(*_args: object, **_kwargs: object) -> object:
        raise SupervisionInfrastructureError("resolver", detail)

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        execute,
    )
    outcome = invoke_resolution_agent(
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

    assert isinstance(outcome, RetryLaterOutcome)
    return session


def test_fault_text_cannot_produce_a_transport_loop_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _invoke_with_fault_detail(monkeypatch, "HTTP 503: transport_loop_detected")

    assert session.terminal_reason is ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    assert session.terminal_reason is not ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    assert session.charge_conflict_budget is False


def test_the_same_typed_fault_gives_the_same_verdict_for_every_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker_text = _invoke_with_fault_detail(
        monkeypatch, "HTTP 503: transport_loop_detected"
    ).terminal_reason
    benign_text = _invoke_with_fault_detail(
        monkeypatch, "activity relay sender: timed out"
    ).terminal_reason

    assert marker_text is benign_text
