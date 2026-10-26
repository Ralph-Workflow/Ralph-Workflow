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
from ralph.pipeline.conflict_resolution.chain_candidates import (
    remember_dead_surface,
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
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {}})


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
    assert session.terminal_reason is ResolutionTerminationReason.TOOL_SURFACE_DEAD
    assert session.last_failure_cause is ResolutionFailureCause.AGENT_FAILURE
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
