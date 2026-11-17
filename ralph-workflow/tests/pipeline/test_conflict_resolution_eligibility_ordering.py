from __future__ import annotations

import dataclasses
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME
from ralph.pipeline.auto_integrate_conflict_budget import resolve_failure_ledger
from ralph.pipeline.conflict_resolution import bind_active_rebase_resolution_session
from ralph.pipeline.conflict_resolution._errors import RecoveryControllerUnavailableError
from ralph.pipeline.conflict_resolution.chain_candidates import (
    remember_dead_surface,
    session_ledger,
    skipped_candidates,
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
    AgentStatus,
    AttemptFact,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.agents.clock import Clock
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle


def _shared_controller(*, clock: Clock | None = None) -> RecoveryController:
    active = clock if clock is not None else FakeClock()
    ledger = AgentFailureLedger(clock=active, consecutive_limit=1, total_limit=1)
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=active,
            ledger=ledger,
            agent_max_consecutive_unproductive=1,
            agent_max_total_unproductive=1,
        )
    )


def _deps_with_controller(
    config: UnifiedConfig,
    display_ctx: DisplayContext,
    controller: RecoveryController,
) -> PipelineDeps:
    return dataclasses.replace(
        build_default_pipeline_deps(config, display_ctx),
        recovery_controller=controller,
        ledger=controller.ledger,
    )


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {}})


class _RecordingLedger(AgentFailureLedger):
    def __init__(self, *, clock: Clock) -> None:
        super().__init__(clock=clock, consecutive_limit=1, total_limit=1)
        self.status_calls: list[tuple[str, str]] = []

    def status(self, phase: str, agent: str) -> AgentStatus:
        self.status_calls.append((phase, agent))
        return super().status(phase, agent)


def test_skipped_candidates_ledger_takes_precedence_over_registry_capability() -> None:
    clock = FakeClock()
    controller = _shared_controller(clock=clock)
    ledger = controller.ledger
    session = ResolutionSession(recovery_controller=controller, ledger=ledger)

    assert skipped_candidates(session, ("agentA", "agentB")) == frozenset()

    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="agentA",
            artifact_valid=False,
        )
    )
    assert not ledger.is_available(PHASE_RESOLUTION, "agentA")
    assert skipped_candidates(session, ("agentA", "agentB")) == frozenset({"agentA"})

    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "agentB")
    assert skipped_candidates(session, ("agentA", "agentB")) == frozenset({"agentA", "agentB"})


def test_skipped_candidates_does_not_bar_on_non_registry_faults() -> None:
    controller = _shared_controller()
    session = ResolutionSession(recovery_controller=controller, ledger=controller.ledger)

    assert skipped_candidates(session, ("agentA", "agentB")) == frozenset()

    session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
    remember_dead_surface(session, "agentA")
    assert "agentA" not in skipped_candidates(session, ("agentA", "agentB"))

    session.terminal_reason = ResolutionTerminationReason.ATTEMPT_FAILED
    remember_dead_surface(session, "agentA")
    assert "agentA" not in skipped_candidates(session, ("agentA", "agentB"))

    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "agentA")
    assert "agentA" in skipped_candidates(session, ("agentA", "agentB"))


def test_invoke_resolution_agent_ledger_takes_precedence_over_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )

    del monkeypatch
    executed: list[str] = []

    def _stub_runner(effect: object, *args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        if isinstance(effect, InvokeAgentEffect):
            executed.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve conflict", encoding="utf-8")
    config = _config()
    bundle = _policy_bundle()
    console = Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    display_ctx = make_display_context(console=console)
    clock = FakeClock()
    controller = _shared_controller(clock=clock)
    ledger = controller.ledger
    deps = _deps_with_controller(config, display_ctx, controller)
    scope = WorkspaceScope(tmp_path)
    session = ResolutionSession(recovery_controller=controller, ledger=ledger)

    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="failing_agent",
            artifact_valid=False,
        )
    )
    assert not ledger.is_available(PHASE_RESOLUTION, "failing_agent")

    token = install_agent_effect_dependencies(
        agent_runner=_stub_runner,
        recovery_controller=controller,
    )
    try:
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
        assert result.event != PipelineEvent.AGENT_SUCCESS
        assert executed == []
        assert not controller.agent_eligibility(PHASE_RESOLUTION, "failing_agent").is_eligible

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
        assert result.event != PipelineEvent.AGENT_SUCCESS
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
        assert result.event == PipelineEvent.AGENT_SUCCESS
        assert executed == ["healthy_agent"]
    finally:
        reset_agent_effect_dependencies(token)


def test_invoke_resolution_agent_ledger_less_path_ignores_non_registry_faults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )

    del monkeypatch
    executed: list[str] = []

    def _stub_runner(effect: object, *args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        if isinstance(effect, InvokeAgentEffect):
            executed.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve conflict", encoding="utf-8")
    config = _config()
    bundle = _policy_bundle()
    console = Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    display_ctx = make_display_context(console=console)
    controller = _shared_controller()
    deps = _deps_with_controller(config, display_ctx, controller)
    scope = WorkspaceScope(tmp_path)

    session = ResolutionSession(recovery_controller=controller, ledger=controller.ledger)
    session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
    remember_dead_surface(session, "agentA")

    token = install_agent_effect_dependencies(
        agent_runner=_stub_runner,
        recovery_controller=controller,
    )
    try:
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
        assert result.event == PipelineEvent.AGENT_SUCCESS
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
        assert result.event != PipelineEvent.AGENT_SUCCESS
        assert executed == ["agentA"]
    finally:
        reset_agent_effect_dependencies(token)


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_continue_rounds_without_controller_or_ledger_fails_closed": (
        "can_continue_rounds refuses further resolution rounds when neither the shared "
        "RecoveryController nor a ledger is bound, matching ensure_session_controller"
    ),
    "test_session_without_shared_controller_fails_closed": (
        "conflict-resolution sessions no longer build a private RecoveryController; "
        "ensure_session_controller fails closed when the run's shared controller is unreachable"
    ),
    "test_without_shared_controller_dead_surface_fails_closed": (
        "remember_dead_surface requires the session's bound shared RecoveryController and "
        "never records into a private ledger fallback"
    ),
    "test_session_reuses_run_shared_controller_and_ledger": (
        "a session adopts the run's RecoveryController through pipeline_deps and shares its ledger"
    ),
    "test_invoke_resolution_agent_ledger_takes_precedence_over_registry": (
        "cooling / unavailable candidates are refused by the shared effect-executor launch "
        "gate rather than a session pre-launch eligibility fork"
    ),
    "test_invoke_resolution_agent_ledger_less_path_ignores_non_registry_faults": (
        "dead-surface marks still reach the shared launch gate; non-registry faults do not"
    ),
    "test_launch_refusal_consults_the_controller_ledger_when_the_session_has_none": (
        "when the session ledger is None, shared effect-executor launch still consults the "
        "bound RecoveryController ledger and refuses a spent agent without a session pre-refuse"
    ),
}


def test_session_without_shared_controller_fails_closed() -> None:
    session = ResolutionSession()
    with pytest.raises(RecoveryControllerUnavailableError):
        ensure_session_controller(session, _policy_bundle())
    assert session.recovery_controller is None
    assert session.ledger is None


def test_session_reuses_run_shared_controller_and_ledger() -> None:
    bundle = _policy_bundle()
    controller = _shared_controller()
    session = ResolutionSession()
    config = _config()
    display_ctx = make_display_context(
        console=Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    )
    deps = _deps_with_controller(config, display_ctx, controller)
    bound = ensure_session_controller(session, bundle, pipeline_deps=deps, config=config)
    assert bound is controller
    assert session.recovery_controller is controller
    assert session.ledger is controller.ledger
    assert isinstance(session.ledger, AgentFailureLedger)


def test_ledger_is_consulted_first_for_every_candidate_including_dead_surfaces() -> None:
    clock = FakeClock()
    ledger = _RecordingLedger(clock=clock)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            agent_max_consecutive_unproductive=1,
            agent_max_total_unproductive=1,
        )
    )
    session = ResolutionSession(recovery_controller=controller, ledger=ledger)

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

    skipped = skipped_candidates(
        session,
        ("spent_agent", "dead_agent", "registry_only_agent", "healthy_agent"),
    )

    assert "spent_agent" in skipped
    assert "dead_agent" in skipped
    assert "registry_only_agent" in skipped
    assert "healthy_agent" not in skipped

    assert ledger.status_calls == [
        (PHASE_RESOLUTION, "spent_agent"),
        (PHASE_RESOLUTION, "dead_agent"),
        (PHASE_RESOLUTION, "registry_only_agent"),
        (PHASE_RESOLUTION, "healthy_agent"),
    ]


def test_chain_seams_consult_the_controller_ledger_when_the_session_has_none() -> None:
    controller = _shared_controller()
    session = ResolutionSession(recovery_controller=controller, ledger=None)

    assert session_ledger(session) is controller.ledger

    ledger = controller.ledger
    attempts = 0
    while not ledger.status(PHASE_RESOLUTION, "benched_agent").is_spent and attempts < 100:
        ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent="benched_agent",
                artifact_valid=False,
            )
        )
        attempts += 1
    assert ledger.status(PHASE_RESOLUTION, "benched_agent").is_spent

    assert skipped_candidates(session, ("benched_agent", "healthy_agent")) == frozenset(
        {"benched_agent"}
    )


def test_launch_refusal_consults_the_controller_ledger_when_the_session_has_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )

    del monkeypatch
    bundle = _policy_bundle()
    controller = _shared_controller()
    session = ResolutionSession(recovery_controller=controller, ledger=None)

    ledger = controller.ledger
    attempts = 0
    while not ledger.status(PHASE_RESOLUTION, "benched_agent").is_spent and attempts < 100:
        ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent="benched_agent",
                artifact_valid=False,
            )
        )
        attempts += 1
    assert ledger.status(PHASE_RESOLUTION, "benched_agent").is_spent

    executed: list[str] = []

    def _stub_runner(effect: object, *args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        if isinstance(effect, InvokeAgentEffect):
            executed.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve conflict", encoding="utf-8")
    config = _config()
    console = Console(file=StringIO(), force_terminal=False, color_system=None, theme=RALPH_THEME)
    display_ctx = make_display_context(console=console)
    deps = _deps_with_controller(config, display_ctx, controller)
    scope = WorkspaceScope(tmp_path)

    token = install_agent_effect_dependencies(
        agent_runner=_stub_runner,
        recovery_controller=controller,
    )
    try:
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
    finally:
        reset_agent_effect_dependencies(token)

    assert result.event != PipelineEvent.AGENT_SUCCESS
    assert executed == []
    assert not controller.agent_eligibility(PHASE_RESOLUTION, "benched_agent").is_eligible


def test_integration_seam_resolves_the_controller_ledger_for_the_active_session() -> None:
    controller = _shared_controller()
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
    assert "unhealthy_agent" in skipped_candidates(with_ledger, ("unhealthy_agent",))

    without_ledger = ResolutionSession()
    assert session_ledger(without_ledger) is None
    assert (
        skipped_candidates(
            without_ledger,
            ("unhealthy_agent", "unavailable_agent"),
        )
        == frozenset()
    )


def test_without_shared_controller_dead_surface_fails_closed() -> None:
    without_controller = ResolutionSession()
    without_controller.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    with pytest.raises(RecoveryControllerUnavailableError):
        remember_dead_surface(without_controller, "unavailable_agent")
    assert session_ledger(without_controller) is None

def test_continue_rounds_without_controller_or_ledger_fails_closed() -> None:
    session = ResolutionSession()
    assert session.recovery_controller is None
    assert session_ledger(session) is None
    assert can_continue_rounds(session, ("agentA", "agentB")) is False
