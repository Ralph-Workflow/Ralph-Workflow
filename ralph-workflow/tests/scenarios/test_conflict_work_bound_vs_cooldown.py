from __future__ import annotations

from types import SimpleNamespace

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate_budget_seam import observe_conflict_identity
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.run_loop import LoopContext, run_integration_conflict_resolution
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_display import ScenarioDisplay
from tests._scenario_harness import capture_operator_lines, paused_text_rebase, scenario_config
from tests.pipeline._auto_integrate_agent_failure_fixtures import resolution_policy_bundle

pytestmark = pytest.mark.subprocess_e2e

_RESOLUTION_AGENT = "claude"
_COOLDOWN_MARKER = "all resolver agents are on cooldown"
_WORK_BOUND_MARKER = "conflict resolution work bound reached its limit"


class _InstalledRegistry:
    def __init__(self, agents) -> None:
        self._agents = tuple(agents)

    def get(self, agent):
        return object() if agent in self._agents else None


def _seed_identity_repo(root) -> None:
    root.mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    readme.write_text("seed\n", encoding="utf-8")
    repo = Repo.init(root, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgsign", "false")
    repo.index.add(["README.md"])
    repo.index.commit("seed")
    repo.close()


def _cooling_controller(agents) -> RecoveryController:
    clock = FakeClock(start=1000.0)
    entries = {
        agent: UnavailabilityEntry(
            unavailable_until_ms=int(clock.monotonic() * 1000) + 60_000,
            reason=UnavailabilityReason.OUT_OF_CREDITS,
            attempt=0,
            base_backoff_ms=60_000,
            max_backoff_ms=1_800_000,
        )
        for agent in agents
    }
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            event_bus=FailureEventBus(),
            unavailability_entries=entries,
        ),
    )


def _loop_context(root, controller, display, chain=(_RESOLUTION_AGENT,)) -> LoopContext:
    return LoopContext(
        policy_bundle=resolution_policy_bundle(list(chain)),
        workspace_scope=WorkspaceScope(root),
        config=scenario_config(),
        active_display=display,
        display_context=None,
        effective_verbosity=0,
        registry=_InstalledRegistry((_RESOLUTION_AGENT,)),
        effective_pipeline_subscriber=None,
        controller=controller,
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=None,
        sleep=lambda _seconds: None,
        is_quiet=False,
    )


def _operator_lines(display) -> list[str]:
    return [line.content for line in display.lines]


def test_every_resolver_on_cooldown_waits_without_launching(tmp_path):
    root = tmp_path / "repo"
    _seed_identity_repo(root)
    paused_text_rebase(root)
    controller = _cooling_controller((_RESOLUTION_AGENT,))
    display = ScenarioDisplay()
    ctx = _loop_context(root, controller, display)
    rebase = RebaseState(last_target="main", last_conflict_paths=("shared.txt",))

    with capture_operator_lines(display):
        first = run_integration_conflict_resolution(ctx, rebase)
        first_lines = _operator_lines(display)
        display.lines.clear()
        second = run_integration_conflict_resolution(ctx, rebase)
        second_lines = _operator_lines(display)

    assert first is False and second is False, "a cooling chain must not resolve the conflict"
    assert ctx.resolver_cooldown_wait_reason, "the cooldown wait must be recorded on the context"

    waiting = [line for line in (*first_lines, *second_lines) if _COOLDOWN_MARKER in line]
    assert len(waiting) == 1, f"the wait status must be emitted once, got {waiting}"
    assert "next-eligible: claude" in waiting[0], (
        f"the wait status must name who is next eligible, got {waiting[0]}"
    )
    assert "until-when:" in waiting[0], f"the wait status must say when, got {waiting[0]}"
    assert "resume point:" in waiting[0], (
        f"the wait status must keep a resume point, got {waiting[0]}"
    )
    assert _WORK_BOUND_MARKER not in waiting[0], (
        "the cooldown wait must not be reported as a work bound that reached its limit"
    )


def test_work_bound_reached_escalates_instead_of_waiting(tmp_path):
    root = tmp_path / "repo"
    _seed_identity_repo(root)
    paused_text_rebase(root)
    controller = _cooling_controller(())
    display = ScenarioDisplay()
    ctx = _loop_context(root, controller, display)
    ctx.controller = SimpleNamespace(ledger=None)
    identity = observe_conflict_identity(root, "main")
    rebase = RebaseState(
        last_target="main",
        last_conflict_paths=("shared.txt",),
        last_conflict_feature_sha=identity.feature_sha,
        last_conflict_target_sha=identity.target_sha,
        resolution_exhausted=True,
        consecutive_conflicts=3,
    )

    with capture_operator_lines(display):
        first = run_integration_conflict_resolution(ctx, rebase)
        first_lines = _operator_lines(display)
        display.lines.clear()
        second = run_integration_conflict_resolution(ctx, rebase)
        second_lines = _operator_lines(display)

    assert first is False and second is False, "a work bound at its limit must not resolve"
    escalated = [line for line in (*first_lines, *second_lines) if _WORK_BOUND_MARKER in line]
    assert len(escalated) == 1, f"the escalation must be emitted once, got {escalated}"
    assert "escalating to the next resolution rung" in escalated[0], (
        f"the work bound denial must name the next rung, got {escalated[0]}"
    )
    parked = [
        line
        for line in (*first_lines, *second_lines)
        if _COOLDOWN_MARKER in line or "operator action" in line.lower()
    ]
    assert not parked, f"a work bound at its limit must not park for the operator, got {parked}"
