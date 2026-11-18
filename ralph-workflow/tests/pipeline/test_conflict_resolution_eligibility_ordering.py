from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_conflict_budget import resolve_failure_ledger
from ralph.pipeline.conflict_resolution import bind_active_rebase_resolution_session
from ralph.pipeline.conflict_resolution.chain_candidates import (
    remember_dead_surface,
    session_ledger,
)
from ralph.pipeline.conflict_resolution.driver import can_continue_rounds
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ResolutionTerminationReason,
    ensure_session_controller,
    invoke_resolution_agent,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import build_default_pipeline_deps
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    Cooldown,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {}})


def _cooling(session: ResolutionSession, candidates: tuple[str, ...]) -> dict[str, str]:
    ledger = session_ledger(session)
    assert ledger is not None
    cooling: dict[str, str] = {}
    for name in candidates:
        status = ledger.status(PHASE_RESOLUTION, name)
        if isinstance(status, Cooldown):
            assert status.until_ms > int(ledger.clock.monotonic() * 1000)
            cooling[name] = status.reason
    return cooling


def _advance_past_every_cooldown(
    clock: FakeClock, session: ResolutionSession, candidates: tuple[str, ...]
) -> None:
    ledger = session_ledger(session)
    assert ledger is not None
    now_ms = int(clock.monotonic() * 1000)
    latest_ms = max(
        status.until_ms
        for status in (ledger.status(PHASE_RESOLUTION, name) for name in candidates)
        if isinstance(status, Cooldown)
    )
    clock.advance((latest_ms - now_ms) / 1000.0)


def test_ledger_and_registry_faults_start_expiring_cooldowns() -> None:
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)
    session = ResolutionSession(ledger=ledger)
    candidates = ("agentA", "agentB")

    assert _cooling(session, candidates) == {}

    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="agentA",
            artifact_valid=False,
        )
    )
    assert not ledger.is_available(PHASE_RESOLUTION, "agentA")
    assert _cooling(session, candidates) == {"agentA": "unproductive_limit"}

    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "agentB")
    assert _cooling(session, candidates) == {
        "agentA": "unproductive_limit",
        "agentB": "candidate_unavailable",
    }

    _advance_past_every_cooldown(clock, session, candidates)

    assert ledger.is_available(PHASE_RESOLUTION, "agentA")
    assert ledger.is_available(PHASE_RESOLUTION, "agentB")


def test_only_a_candidate_unavailable_fault_starts_a_cooldown() -> None:
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock)
    session = ResolutionSession(ledger=ledger)
    candidates = ("agentA", "agentB")

    assert _cooling(session, candidates) == {}

    session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
    remember_dead_surface(session, "agentA")
    assert _cooling(session, candidates) == {}

    session.terminal_reason = ResolutionTerminationReason.ATTEMPT_FAILED
    remember_dead_surface(session, "agentA")
    assert _cooling(session, candidates) == {}

    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "agentA")
    assert _cooling(session, candidates) == {"agentA": "candidate_unavailable"}

    _advance_past_every_cooldown(clock, session, candidates)

    assert _cooling(session, candidates) == {}


def test_invoke_resolution_agent_ledger_takes_precedence_over_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executed: list[str] = []

    def _fake_execute(*args: object, **kwargs: object) -> PipelineEvent:
        if args and isinstance(args[0], InvokeAgentEffect):
            executed.append(args[0].agent_name)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _fake_execute,
    )

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve conflict", encoding="utf-8")
    config = _config()
    bundle = _policy_bundle()
    console = Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    display_ctx = make_display_context(console=console)
    deps = build_default_pipeline_deps(config, display_ctx)
    scope = WorkspaceScope(tmp_path)

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)
    session = ResolutionSession(ledger=ledger)

    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="failing_agent",
            artifact_valid=False,
        )
    )
    assert not ledger.is_available(PHASE_RESOLUTION, "failing_agent")

    result = invoke_resolution_agent(
        agent_name="failing_agent",
        prompt_path=prompt,
        config=config,
        pipeline_deps=deps,
        workspace_scope=scope,
        policy_bundle=bundle,
        display=None,
        display_context=display_ctx,
        session=session,
    )
    assert result.succeeded is False
    assert executed == []
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    assert session.last_failure_cause is ResolutionFailureCause.NONE
    assert session.charge_conflict_budget is False

    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "unsupported_agent")
    result = invoke_resolution_agent(
        agent_name="unsupported_agent",
        prompt_path=prompt,
        config=config,
        pipeline_deps=deps,
        workspace_scope=scope,
        policy_bundle=bundle,
        display=None,
        display_context=display_ctx,
        session=session,
    )
    assert result.succeeded is False
    assert executed == []

    result = invoke_resolution_agent(
        agent_name="healthy_agent",
        prompt_path=prompt,
        config=config,
        pipeline_deps=deps,
        workspace_scope=scope,
        policy_bundle=bundle,
        display=None,
        display_context=display_ctx,
        session=session,
    )
    assert result.succeeded is True
    assert executed == ["healthy_agent"]


def test_invoke_resolution_agent_ledger_less_path_ignores_non_registry_faults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executed: list[str] = []

    def _fake_execute(*args: object, **kwargs: object) -> PipelineEvent:
        if args and isinstance(args[0], InvokeAgentEffect):
            executed.append(args[0].agent_name)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _fake_execute,
    )

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve conflict", encoding="utf-8")
    config = _config()
    bundle = _policy_bundle()
    console = Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    display_ctx = make_display_context(console=console)
    deps = build_default_pipeline_deps(config, display_ctx)
    scope = WorkspaceScope(tmp_path)

    session = ResolutionSession()
    session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
    remember_dead_surface(session, "agentA")

    result = invoke_resolution_agent(
        agent_name="agentA",
        prompt_path=prompt,
        config=config,
        pipeline_deps=deps,
        workspace_scope=scope,
        policy_bundle=bundle,
        display=None,
        display_context=display_ctx,
        session=session,
    )
    assert result.succeeded is True
    assert executed == ["agentA"]

    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "agentB")
    result = invoke_resolution_agent(
        agent_name="agentB",
        prompt_path=prompt,
        config=config,
        pipeline_deps=deps,
        workspace_scope=scope,
        policy_bundle=bundle,
        display=None,
        display_context=display_ctx,
        session=session,
    )
    assert result.succeeded is False
    assert executed == ["agentA"]


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_skipped_candidates_ledger_takes_precedence_over_registry_capability": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates and its "
        "permanent per-run bar are gone, so the test asserts the unproductive_limit and "
        "candidate_unavailable cooldowns and that both expire on the fake clock"
    ),
    "test_ledger_and_registry_faults_start_expiring_cooldowns": (
        "operator rule: an agent is either on cooldown or not; replaces the skipped_candidates "
        "precedence test with the expiring cooldown contract"
    ),
    "test_skipped_candidates_does_not_bar_on_non_registry_faults": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates is gone, so "
        "the test asserts that only a candidate_unavailable fault starts a cooldown and that "
        "it expires on the fake clock"
    ),
    "test_only_a_candidate_unavailable_fault_starts_a_cooldown": (
        "operator rule: an agent is either on cooldown or not; replaces the skipped_candidates "
        "non-registry fault test with the expiring cooldown contract"
    ),
    "test_ledger_is_consulted_first_for_every_candidate_including_dead_surfaces": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates and its "
        "status call order are gone, so the test asserts each candidate cooldown status and "
        "that every cooldown expires on the fake clock"
    ),
    "test_ledger_reports_every_candidate_including_dead_surfaces_as_cooling": (
        "operator rule: an agent is either on cooldown or not; replaces the skipped_candidates "
        "consult-order test with the expiring cooldown contract"
    ),
    "test_chain_seams_consult_the_controller_ledger_when_the_session_has_none": (
        "operator rule: an agent is either on cooldown or not; the benched agent reaches an "
        "expiring unproductive_limit cooldown instead of Spent, and the test asserts it expires"
    ),
    "test_launch_refusal_consults_the_controller_ledger_when_the_session_has_none": (
        "operator rule: an agent is either on cooldown or not; the benched agent reaches an "
        "unproductive_limit cooldown instead of Spent before the launch is refused"
    ),
    "test_continue_rounds_without_controller_or_ledger_fails_closed": (
        "can_continue_rounds refuses further resolution rounds when neither the shared "
        "RecoveryController nor a ledger is bound, matching ensure_session_controller"
    ),
    "test_session_controller_creation_guarantees_ledger": (
        "restores the entry-point controller fallback that the gated 91a0ae83d repair "
        "recorded and 0afc2dac3 reverted; a session without a shared "
        "RecoveryController builds its conflict-resolution controller instead of "
        "raising, which the in-gate attribution, round-cap and shared-ledger contracts "
        "depend on"
    ),
    "test_without_a_ledger_only_the_registry_capability_exclusion_applies": (
        "restores the entry-point controller fallback that the gated 91a0ae83d repair "
        "recorded and 0afc2dac3 reverted; a session without a shared "
        "RecoveryController builds its conflict-resolution controller instead of "
        "raising, which the in-gate attribution, round-cap and shared-ledger contracts "
        "depend on; a ledger-less session again reports no ledger before a controller "
        "is built; operator rule: an agent is either on cooldown or not; skipped_candidates "
        "is gone, so the test reads the cooldown reasons through the session ledger"
    ),
}


def test_session_controller_creation_guarantees_ledger() -> None:
    bundle = _policy_bundle()
    session = ResolutionSession()
    controller = ensure_session_controller(session, bundle)
    assert session.recovery_controller is controller
    ledger = session.ledger
    assert ledger is not None
    assert isinstance(ledger, AgentFailureLedger)
    assert ledger is controller.ledger


def test_ledger_reports_every_candidate_including_dead_surfaces_as_cooling() -> None:
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)
    session = ResolutionSession(ledger=ledger)
    candidates = ("spent_agent", "dead_agent", "registry_only_agent", "healthy_agent")

    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "dead_agent")
    remember_dead_surface(session, "registry_only_agent")

    for spent in ("spent_agent", "dead_agent"):
        ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent=spent,
                artifact_valid=False,
            )
        )

    cooling = _cooling(session, candidates)

    assert cooling["spent_agent"] == "unproductive_limit"
    assert "dead_agent" in cooling
    assert cooling["registry_only_agent"] == "candidate_unavailable"
    assert "healthy_agent" not in cooling

    _advance_past_every_cooldown(clock, session, candidates)

    assert all(ledger.is_available(PHASE_RESOLUTION, name) for name in candidates)


def _cool_benched_agent(ledger: AgentFailureLedger) -> Cooldown:
    attempts = 0
    while ledger.status(PHASE_RESOLUTION, "benched_agent").is_eligible and attempts < 100:
        ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent="benched_agent",
                artifact_valid=False,
            )
        )
        attempts += 1
    status = ledger.status(PHASE_RESOLUTION, "benched_agent")
    assert isinstance(status, Cooldown)
    assert status.reason == "unproductive_limit"
    return status


def test_chain_seams_consult_the_controller_ledger_when_the_session_has_none() -> None:
    bundle = _policy_bundle()
    clock = FakeClock()
    controller = ensure_session_controller(
        ResolutionSession(ledger=AgentFailureLedger(clock=clock)), bundle
    )
    session = ResolutionSession(recovery_controller=controller, ledger=None)

    assert session_ledger(session) is controller.ledger

    _cool_benched_agent(controller.ledger)

    candidates = ("benched_agent", "healthy_agent")
    assert _cooling(session, candidates) == {"benched_agent": "unproductive_limit"}

    _advance_past_every_cooldown(clock, session, candidates)

    assert _cooling(session, candidates) == {}


def test_launch_refusal_consults_the_controller_ledger_when_the_session_has_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _policy_bundle()
    controller = ensure_session_controller(ResolutionSession(), bundle)
    session = ResolutionSession(recovery_controller=controller, ledger=None)

    _cool_benched_agent(controller.ledger)

    executed: list[str] = []

    def _fake_execute(*args: object, **kwargs: object) -> PipelineEvent:
        if args and isinstance(args[0], InvokeAgentEffect):
            executed.append(args[0].agent_name)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _fake_execute,
    )

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve conflict", encoding="utf-8")
    config = _config()
    console = Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    display_ctx = make_display_context(console=console)
    deps = build_default_pipeline_deps(config, display_ctx)
    scope = WorkspaceScope(tmp_path)

    result = invoke_resolution_agent(
        agent_name="benched_agent",
        prompt_path=prompt,
        config=config,
        pipeline_deps=deps,
        workspace_scope=scope,
        policy_bundle=bundle,
        display=None,
        display_context=display_ctx,
        session=session,
    )

    assert result.succeeded is False
    assert executed == []
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    assert session.last_failure_cause is ResolutionFailureCause.NONE
    assert session.charge_conflict_budget is False


def test_integration_seam_resolves_the_controller_ledger_for_the_active_session() -> None:
    bundle = _policy_bundle()
    controller = ensure_session_controller(ResolutionSession(), bundle)
    session = ResolutionSession(recovery_controller=controller, ledger=None)

    with bind_active_rebase_resolution_session(session):
        assert resolve_failure_ledger(None) is controller.ledger

    injected = AgentFailureLedger(clock=FakeClock())
    with bind_active_rebase_resolution_session(session):
        assert resolve_failure_ledger(injected) is injected


def test_without_a_ledger_only_the_registry_capability_exclusion_applies() -> None:
    ledger = AgentFailureLedger(clock=FakeClock(), consecutive_limit=1, total_limit=1)
    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="unhealthy_agent",
            artifact_valid=False,
        )
    )
    assert not ledger.is_available(PHASE_RESOLUTION, "unhealthy_agent")

    with_ledger = ResolutionSession(ledger=ledger)
    assert _cooling(with_ledger, ("unhealthy_agent",)) == {"unhealthy_agent": "unproductive_limit"}

    without_ledger = ResolutionSession()
    assert session_ledger(without_ledger) is None
    without_ledger.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(without_ledger, "unavailable_agent")
    assert _cooling(without_ledger, ("unhealthy_agent", "unavailable_agent")) == {
        "unavailable_agent": "candidate_unavailable"
    }


def test_continue_rounds_without_controller_or_ledger_fails_closed() -> None:
    session = ResolutionSession()
    assert session.recovery_controller is None
    assert session_ledger(session) is None
    assert can_continue_rounds(session, ("agentA", "agentB")) is False
