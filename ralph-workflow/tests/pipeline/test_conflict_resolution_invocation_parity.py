from __future__ import annotations

import ast
import dataclasses
import inspect
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ralph.agents.timeout_clock import FakeClock
from ralph.config.conflict_resolution_config import ConflictResolutionConfig
from ralph.config.general_config import GeneralConfig
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import status_text
from ralph.pipeline.attempt_result import AttemptResult
from ralph.pipeline.conflict_resolution import session as conflict_session
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ensure_session_controller,
    invoke_resolution_agent,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.resolution_outcome import (
    RetryLaterOutcome,
    is_stop_resolution_outcome,
)
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attempt_outcome import AttemptOutcome
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope
from tests._conflict_resolution_phase_parity_seams import _config, _policy_bundle
from tests._pipeline_deps_factory import make_test_pipeline_deps

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_invoke_resolution_agent_returns_shared_attempt_result": (
        "invariant 13: invoke helper propagates AttemptResult; StopResolutionOutcome "
        "mapping belongs after invocation in integration"
    ),
    "test_invoke_default_requires_completion_evidence_like_ordinary": (
        "resolver require_completion_evidence default must match InvokeAgentEffect "
        "STANDARD default True"
    ),
    "test_invoke_does_not_remap_conflict_inactivity_into_general_idle": (
        "resolver must not copy conflict inactivity into general.agent_idle_timeout_seconds"
    ),
    "test_conflict_config_rejects_removed_timing_keys": (
        "status_interval_seconds and inactivity_timeout_seconds are removed resolver-only "
        "timing forks; idle comes from [general]"
    ),
    "test_session_module_has_no_finalize_resolution_attempt_failure": (
        "dual finalization via finalize_resolution_attempt_failure is removed; "
        "failures finalize only through run_agent_attempt → finalize_attempt"
    ),
    "test_session_module_has_no_private_launch_eligibility_gate": (
        "invariant 13 aspect-2: conflict_resolution owns no private launch gate; "
        "refusal is only via shared effect_executor / run_agent_attempt"
    ),

    "test_retry_later_until_comes_from_shared_cooldown_when_deadline_needed": (
        "RetryLaterOutcome.until must be taken from the shared cooldown wait when a "
        "deadline is required"
    ),
    "test_agent_lifecycle_status_uses_status_text": (
        "resolver agent-lifecycle reporting must use the ordinary status_text reporter"
    ),
}


def test_session_module_has_no_finalize_resolution_attempt_failure() -> None:
    source = Path(inspect.getsourcefile(conflict_session) or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "finalize_resolution_attempt_failure" not in names
    assert "finalize_resolution_attempt_failure" not in conflict_session.__all__


def test_session_module_has_no_private_launch_eligibility_gate() -> None:
    source = Path(inspect.getsourcefile(conflict_session) or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_candidate_cannot_be_launched" not in names
    assert "_refusal_attempt_result" not in names
    assert "cannot be launched" not in source


def test_invoke_resolution_agent_returns_shared_attempt_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )

    def _stub_runner(effect: object, *args: object, **kwargs: object) -> PipelineEvent:
        del effect, args, kwargs
        return PipelineEvent.AGENT_SUCCESS

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    clock = FakeClock()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=3),
        )
    )
    context = make_display_context(console=Console(file=StringIO(), force_terminal=False))
    deps = dataclasses.replace(
        make_test_pipeline_deps(context, recovery_clock=clock),
        recovery_controller=controller,
        ledger=controller.ledger,
    )
    session = ResolutionSession()
    ensure_session_controller(session, _policy_bundle(), pipeline_deps=deps, config=_config())
    token = install_agent_effect_dependencies(
        agent_runner=_stub_runner,
        recovery_controller=controller,
    )
    try:
        result = invoke_resolution_agent(
            agent_name="resolver",
            prompt_path=prompt,
            config=_config(),
            pipeline_deps=deps,
            workspace_scope=WorkspaceScope(tmp_path),
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=context,
            session=session,
        )
    finally:
        reset_agent_effect_dependencies(token)

    assert isinstance(result, AttemptResult)
    assert not is_stop_resolution_outcome(result)
    assert result.event == PipelineEvent.AGENT_SUCCESS


def test_invoke_default_requires_completion_evidence_like_ordinary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[InvokeAgentEffect] = []

    def _run_attempt(
        _entry_point: object,
        *,
        effect: InvokeAgentEffect,
        **_kwargs: object,
    ) -> AttemptResult:
        captured.append(effect)
        outcome = AttemptOutcome(
            kind=AttemptOutcomeKind.COMPLETED,
            issuer=None,
            verdict=None,
            reason="",
        )
        return AttemptResult(
            event=PipelineEvent.AGENT_SUCCESS,
            facts=AttemptFacts(artifact_valid=True, workspace_changed=True),
            outcome=outcome,
            attempt_outcomes=((1, outcome),),
        )

    monkeypatch.setattr(
        conflict_session,
        "ensure_session_controller",
        lambda *_a, **_k: RecoveryController(options=RecoveryControllerOptions(clock=FakeClock())),
    )
    monkeypatch.setattr(conflict_session, "run_agent_attempt", _run_attempt)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    invoke_resolution_agent(
        agent_name="resolver",
        prompt_path=prompt,
        config=_config(),
        workspace_scope=WorkspaceScope(tmp_path),
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=make_display_context(),
    )
    assert captured
    assert captured[0].requires_completion_evidence is True
    assert (
        captured[0].requires_completion_evidence
        is InvokeAgentEffect(
            agent_name="x",
            phase=PHASE_RESOLUTION,
            prompt_file="p",
        ).requires_completion_evidence
    )


def test_invoke_does_not_remap_conflict_inactivity_into_general_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    general_idle = 13.0
    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=general_idle))
    seen_idle: list[float | None] = []

    def _run_attempt(
        _entry_point: object,
        *,
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        **_kwargs: object,
    ) -> AttemptResult:
        del effect
        seen_idle.append(config.general.agent_idle_timeout_seconds)
        outcome = AttemptOutcome(
            kind=AttemptOutcomeKind.COMPLETED,
            issuer=None,
            verdict=None,
            reason="",
        )
        return AttemptResult(
            event=PipelineEvent.AGENT_SUCCESS,
            facts=AttemptFacts(artifact_valid=True),
            outcome=outcome,
            attempt_outcomes=((1, outcome),),
        )

    monkeypatch.setattr(
        conflict_session,
        "ensure_session_controller",
        lambda *_a, **_k: RecoveryController(options=RecoveryControllerOptions(clock=FakeClock())),
    )
    monkeypatch.setattr(conflict_session, "run_agent_attempt", _run_attempt)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    invoke_resolution_agent(
        agent_name="resolver",
        prompt_path=prompt,
        config=config,
        workspace_scope=WorkspaceScope(tmp_path),
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=make_display_context(),
        session=ResolutionSession(),
    )
    assert seen_idle == [general_idle]
    assert config.general.agent_idle_timeout_seconds == general_idle
    assert not hasattr(conflict_session, "_conflict_config_with_inactivity")


def test_conflict_config_rejects_removed_timing_keys() -> None:
    for key in ("status_interval_seconds", "inactivity_timeout_seconds"):
        with pytest.raises(ValueError, match=key):
            ConflictResolutionConfig.model_validate({key: 30.0})
    names = set(ConflictResolutionConfig.model_fields)
    assert "status_interval_seconds" not in names
    assert "inactivity_timeout_seconds" not in names


def test_retry_later_until_comes_from_shared_cooldown_when_deadline_needed() -> None:
    from ralph.pipeline.conflict_resolution.driver import stop_outcome_from_attempt_result

    clock = FakeClock()
    clock.set_epoch(1_000.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=5),
            agent_max_consecutive_unproductive=1,
            agent_max_total_unproductive=5,
        )
    )
    controller.finalize_attempt(
        PHASE_RESOLUTION,
        "resolver",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
        exc=ConnectionError("provider down"),
    )
    wait_ms = controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["resolver"])
    assert wait_ms > 0
    session = ResolutionSession(recovery_controller=controller)
    outcome = AttemptOutcome(
        kind=AttemptOutcomeKind.RETRYABLE_FAILURE,
        issuer=None,
        verdict=None,
        reason="provider down",
    )
    attempt = AttemptResult(
        event=PipelineEvent.AGENT_FAILURE,
        facts=AttemptFacts(artifact_valid=False),
        outcome=outcome,
        attempt_outcomes=((1, outcome),),
    )
    mapped = stop_outcome_from_attempt_result(session, attempt, agent_name="resolver")
    assert isinstance(mapped, RetryLaterOutcome)
    assert mapped.until is not None
    assert mapped.on_change is None
    assert mapped.until == pytest.approx(clock.epoch() + wait_ms / 1000.0)


def test_agent_lifecycle_status_uses_status_text() -> None:
    from ralph.pipeline.conflict_resolution.status import emit_agent_lifecycle_status

    lines: list[str] = []

    class _Display:
        def emit(self, unit_id: object, line: str) -> None:
            del unit_id
            lines.append(line)

        def emit_warn_line(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("agent lifecycle must not use warn-channel emit")

    emit_agent_lifecycle_status(_Display(), "WAITING", "cooldown; retrying", "yellow")
    expected = status_text("WAITING", "cooldown; retrying", "yellow")
    assert lines == [expected]
