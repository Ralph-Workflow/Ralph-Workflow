"""Tests verifying ledger-first eligibility ordering in conflict resolution.

Enforces:
1. When a failure ledger is present, it is consulted first as the sole authority
   for agent health and availability.
2. Registry capability is checked secondary to ledger health for permanent
   registry-unavailability exclusions (via ``remember_dead_surface`` on
   ``CANDIDATE_UNAVAILABLE``).
3. The unreachable ledger-less fallback branch is deleted; non-registry failures
   do not bar candidates through legacy fallback mechanisms.
4. Ordering and precedence are verified strictly through the public seam
   (``skipped_candidates``, ``remember_dead_surface``, ``invoke_resolution_agent``,
   and ``ensure_session_controller``).
5. ONE resolver -- ``session_ledger`` -- answers "which ledger speaks for agent
   health", and every seam that judges a candidate's health (the chain, the
   launch refusal and the integration seam) goes through it, so no second code
   path can reach a different verdict about the same agent.
"""

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
from ralph.pipeline.rebase_state import ResolutionFailureCause
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
    """A failure ledger that records every status question it is asked.

    It answers exactly what the shipped ledger answers -- only the
    questions are remembered, so a test can see WHICH candidates the
    ledger was consulted about, and how often, without touching any
    private state.
    """

    def __init__(self, *, clock: Clock) -> None:
        super().__init__(clock=clock, consecutive_limit=1, total_limit=1)
        self.status_calls: list[tuple[str, str]] = []

    def status(self, phase: str, agent: str) -> AgentStatus:
        self.status_calls.append((phase, agent))
        return super().status(phase, agent)


def test_skipped_candidates_ledger_takes_precedence_over_registry_capability() -> None:
    """Ledger availability is consulted first; registry capability is secondary."""
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)
    session = ResolutionSession(ledger=ledger)

    # 1. Both agents eligible in ledger, neither barred by registry -> none skipped
    assert skipped_candidates(session, ("agentA", "agentB")) == frozenset()

    # 2. agentA is Spent in the ledger -> barred by ledger authority
    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="agentA",
            artifact_valid=False,
        )
    )
    assert not ledger.is_available(PHASE_RESOLUTION, "agentA")
    assert skipped_candidates(session, ("agentA", "agentB")) == frozenset({"agentA"})

    # 3. agentB fails with CANDIDATE_UNAVAILABLE (registry cannot produce it)
    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "agentB")
    assert skipped_candidates(session, ("agentA", "agentB")) == frozenset({"agentA", "agentB"})


def test_skipped_candidates_does_not_bar_on_non_registry_faults() -> None:
    """Non-registry faults do not bar candidate exclusion through legacy fallback."""
    session = ResolutionSession()

    # Initial state: neither is skipped
    assert skipped_candidates(session, ("agentA", "agentB")) == frozenset()

    # Attempt failed / infrastructure fault does not bar the candidate
    session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
    remember_dead_surface(session, "agentA")
    assert "agentA" not in skipped_candidates(session, ("agentA", "agentB"))

    session.terminal_reason = ResolutionTerminationReason.ATTEMPT_FAILED
    remember_dead_surface(session, "agentA")
    assert "agentA" not in skipped_candidates(session, ("agentA", "agentB"))

    # But permanent registry unavailability DOES bar the candidate
    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "agentA")
    assert "agentA" in skipped_candidates(session, ("agentA", "agentB"))


def test_invoke_resolution_agent_ledger_takes_precedence_over_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ledger refusal takes precedence over registry through the invoke seam."""
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

    # 1. Agent unavailable in ledger -> launch refused immediately
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
    # The ledger refused the launch: the candidate was UNAVAILABLE, not
    # a dead tool surface, and no agent failure occurred (the agent
    # never ran). The refusal charges no conflict budget.
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    assert session.last_failure_cause is ResolutionFailureCause.NONE
    assert session.charge_conflict_budget is False

    # 2. Agent available in ledger, but barred by registry via CANDIDATE_UNAVAILABLE -> refused
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

    # 3. Healthy agent -> launch proceeds
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
    """When session.ledger is None, launch is not blocked by non-registry faults."""
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

    # session.ledger is None; non-registry fault recorded
    session = ResolutionSession()
    session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
    remember_dead_surface(session, "agentA")

    # Launch proceeds because non-registry fault does not bar the candidate
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

    # Permanent registry unavailability DOES block launch
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
    """ensure_session_controller always equips ResolutionSession with an AgentFailureLedger."""
    bundle = _policy_bundle()
    session = ResolutionSession()
    controller = ensure_session_controller(session, bundle)
    assert session.recovery_controller is controller
    ledger = session.ledger
    assert ledger is not None
    assert isinstance(ledger, AgentFailureLedger)
    assert ledger is controller.ledger


def test_ledger_is_consulted_first_for_every_candidate_including_dead_surfaces() -> None:
    """The ledger is asked about EVERY candidate before the registry exclusion.

    Pins the ORDER: the health verdict is read first, for every candidate
    -- a candidate the registry has already excluded included -- so the
    registry bar can never be the only thing that was looked at. Each
    candidate is asked about exactly once.
    """
    clock = FakeClock()
    ledger = _RecordingLedger(clock=clock)
    session = ResolutionSession(ledger=ledger)

    # The registry cannot produce these names: a registry-capability bar.
    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "dead_agent")
    remember_dead_surface(session, "registry_only_agent")

    # The ledger has spent these agents: a health bar.
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

    # The ledger's verdict alone bars the spent candidate.
    assert "spent_agent" in skipped
    # The ledger's verdict still holds for a candidate that is ALSO a dead
    # surface, and the ledger status WAS read for it.
    assert "dead_agent" in skipped
    # The registry-capability exclusion alone bars the dead surface.
    assert "registry_only_agent" in skipped
    # A healthy candidate is launched.
    assert "healthy_agent" not in skipped

    # The ledger was consulted first, for every candidate, exactly once.
    assert ledger.status_calls == [
        (PHASE_RESOLUTION, "spent_agent"),
        (PHASE_RESOLUTION, "dead_agent"),
        (PHASE_RESOLUTION, "registry_only_agent"),
        (PHASE_RESOLUTION, "healthy_agent"),
    ]


def test_chain_seams_consult_the_controller_ledger_when_the_session_has_none() -> None:
    """A session whose CONTROLLER holds the ledger is judged by that ledger.

    The session's own ledger field is empty here, so a seam that reads
    only that field would find no authority and would launch an agent
    the ledger has already benched.
    """
    bundle = _policy_bundle()
    controller = ensure_session_controller(ResolutionSession(), bundle)
    session = ResolutionSession(recovery_controller=controller, ledger=None)

    # The controller's ledger is the health authority for this session.
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

    # The chain bars the benched candidate and launches the healthy one.
    assert skipped_candidates(session, ("benched_agent", "healthy_agent")) == frozenset(
        {"benched_agent"}
    )


def test_launch_refusal_consults_the_controller_ledger_when_the_session_has_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The launch refusal uses the controller's ledger too, and says the same thing.

    A refusal is Ralph's own verdict about an agent's health, so it must
    come from the same authority the chain used -- and it must carry the
    same termination semantics a ledger refusal has always carried.
    """
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
    # The refusal is the OWNER's verdict about the agent's health: the
    # candidate is unavailable, the tool surface is alive, and no agent
    # failure is recorded for an agent that never ran.
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    assert session.last_failure_cause is ResolutionFailureCause.NONE
    assert session.charge_conflict_budget is False


def test_integration_seam_resolves_the_controller_ledger_for_the_active_session() -> None:
    """The integration seam gates the resolver against the SAME ledger.

    The seam reads the ACTIVE session's ledger, so a session whose own
    ledger field is empty but whose controller holds one must resolve to
    that controller's ledger -- otherwise the seam would let a benched
    resolver run while the resolver itself is refused.
    """
    bundle = _policy_bundle()
    controller = ensure_session_controller(ResolutionSession(), bundle)
    session = ResolutionSession(recovery_controller=controller, ledger=None)

    with bind_active_rebase_resolution_session(session):
        assert resolve_failure_ledger(None) is controller.ledger

    # An injected ledger still wins: the seam never overrides its caller.
    injected = AgentFailureLedger(clock=FakeClock())
    with bind_active_rebase_resolution_session(session):
        assert resolve_failure_ledger(injected) is injected


def test_without_a_ledger_only_the_registry_capability_exclusion_applies() -> None:
    """No ledger means no health authority: only the registry bar applies.

    Pins the documented fallback: with no ledger there is nobody to ask
    about an agent's health, so NO health exclusion is applied at all --
    not a cooldown bar, not a spent bar. The registry-capability
    exclusion is not a health judgement and still applies.
    """
    ledger = AgentFailureLedger(clock=FakeClock(), consecutive_limit=1, total_limit=1)
    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="unhealthy_agent",
            artifact_valid=False,
        )
    )
    assert not ledger.is_available(PHASE_RESOLUTION, "unhealthy_agent")

    # WITH a ledger the health exclusion applies.
    with_ledger = ResolutionSession(ledger=ledger)
    assert "unhealthy_agent" in skipped_candidates(with_ledger, ("unhealthy_agent",))

    # WITHOUT a ledger the same agent is not barred on health grounds.
    without_ledger = ResolutionSession()
    assert session_ledger(without_ledger) is None
    without_ledger.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(without_ledger, "unavailable_agent")
    assert skipped_candidates(
        without_ledger,
        ("unhealthy_agent", "unavailable_agent"),
    ) == frozenset({"unavailable_agent"})
