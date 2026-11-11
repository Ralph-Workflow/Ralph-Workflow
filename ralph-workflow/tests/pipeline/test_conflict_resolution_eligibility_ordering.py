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
    skipped_candidates,
)
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
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.agents.clock import Clock
    from ralph.policy.models import PolicyBundle


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
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)
    session = ResolutionSession(ledger=ledger)

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
    session = ResolutionSession()

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
    assert result is False
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
    assert result is False
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
    assert result is True
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
    assert result is True
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
    assert result is False
    assert executed == ["agentA"]


def test_session_controller_creation_guarantees_ledger() -> None:
    bundle = _policy_bundle()
    session = ResolutionSession()
    controller = ensure_session_controller(session, bundle)
    assert session.recovery_controller is controller
    ledger = session.ledger
    assert ledger is not None
    assert isinstance(ledger, AgentFailureLedger)
    assert ledger is controller.ledger


def test_ledger_is_consulted_first_for_every_candidate_including_dead_surfaces() -> None:
    clock = FakeClock()
    ledger = _RecordingLedger(clock=clock)
    session = ResolutionSession(ledger=ledger)

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
    bundle = _policy_bundle()
    controller = ensure_session_controller(ResolutionSession(), bundle)
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
    bundle = _policy_bundle()
    controller = ensure_session_controller(ResolutionSession(), bundle)
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

    assert result is False
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
    assert "unhealthy_agent" in skipped_candidates(with_ledger, ("unhealthy_agent",))

    without_ledger = ResolutionSession()
    assert session_ledger(without_ledger) is None
    without_ledger.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(without_ledger, "unavailable_agent")
    assert skipped_candidates(
        without_ledger,
        ("unhealthy_agent", "unavailable_agent"),
    ) == frozenset({"unavailable_agent"})
