from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.agents.invoke import AgentInactivityTimeoutError, AttemptEnded, InactivityTimeoutOpts
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.agents.watchdog_fire_reason import WatchdogFireReason
from ralph.config.enums import Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import runner as runner_module
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_entry_point import AgentEntryPoint, registered_runners
from ralph.pipeline.attempt_finalizer import AttemptFinalization
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.effect_executor import (
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import AgentFailureEvent, PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)
from ralph.timeout_defaults import AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

_FALLBACK = "next-agent"
_RUN_ID = "cooldown-main-phase-run"
_CONSECUTIVE_REASON = "consecutive_unproductive_limit_reached"


def _production_phases() -> tuple[str, ...]:
    bundle = cached_default_policy_bundle()
    return tuple(
        name
        for name, phase in bundle.pipeline.phases.items()
        if phase.role != "terminal" and not phase.skip_invocation
    )


def _phase_chain_agents(phase: str) -> list[str]:
    bundle = cached_default_policy_bundle()
    phase_def = bundle.pipeline.phases[phase]
    drain = bundle.agents.agent_drains[phase_def.drain]
    chain = bundle.agents.agent_chains[drain.chain]
    return [*chain.agents, _FALLBACK]


def _phase_bundle(phase: str) -> PolicyBundle:
    bundle = cached_default_policy_bundle()
    phase_def = bundle.pipeline.phases[phase]
    drain = bundle.agents.agent_drains[phase_def.drain]
    chain = bundle.agents.agent_chains[drain.chain]
    extended = chain.model_copy(update={"agents": _phase_chain_agents(phase)})
    return bundle.model_copy(
        update={
            "agents": bundle.agents.model_copy(
                update={
                    "agent_chains": {
                        **bundle.agents.agent_chains,
                        drain.chain: extended,
                    }
                }
            )
        }
    )


def _capture_cooldown_lines(run: Callable[[], object]) -> list[str]:
    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING", format="{message}")
    try:
        run()
    finally:
        logger.remove(sink)
    return [line.strip() for line in lines if "cooldown_decision" in line]


class _MainPhaseHarness:
    def __init__(self, phase: str, workspace_root: Path) -> None:
        self.phase = phase
        self.bundle = _phase_bundle(phase)
        self.agents = _phase_chain_agents(phase)
        self.primary = self.agents[0]
        self.clock = FakeClock(start=0.0)
        readme = workspace_root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        self.controller = RecoveryController(
            options=RecoveryControllerOptions(
                clock=self.clock,
                policy_bundle=self.bundle,
            )
        )
        self.controller.register_phase_chain(phase, self.agents)
        self.config = UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in self.agents})
        self.registry = AgentRegistry.from_config(self.config)
        self.display_context = make_display_context()
        self.display = ParallelDisplay(
            workspace_root=workspace_root,
            display_context=self.display_context,
            is_quiet=True,
        )
        self.deps = replace(
            make_test_pipeline_deps(
                self.display_context,
                recovery_clock=self.clock,
                policy_bundle=self.bundle,
            ),
            recovery_controller=self.controller,
        )
        self.scope = WorkspaceScope(workspace_root)
        self.state = PipelineState(
            phase=phase,
            phase_chains={phase: AgentChainState(agents=list(self.agents))},
            last_connectivity_state="online",
        )
        self.invoked: list[str] = []

    def _stub(self, mode: str):
        def stub(
            effect: InvokeAgentEffect,
            *_args: object,
            **kwargs: object,
        ) -> PipelineEvent | AgentFailureEvent | AttemptEnded:
            agent = getattr(effect, "agent_name", None)
            assert isinstance(agent, str)
            self.invoked.append(agent)
            if mode == "stalled_success":
                return AttemptEnded(
                    facts=AttemptFacts(artifact_valid=False, workspace_changed=False)
                )
            if mode == "no_activity":
                raise AgentInactivityTimeoutError(
                    agent,
                    30.0,
                    [],
                    InactivityTimeoutOpts(reason=WatchdogFireReason.NO_OUTPUT_AT_START),
                )
            finalizer = kwargs.get("attempt_finalizer")
            assert callable(finalizer)
            finalizer(AttemptFinalization(1, True, None, False, None, None))
            return PipelineEvent.AGENT_FAILURE

        return stub

    def dispatch(self) -> InvokeAgentEffect:
        effect = runner_module.call_determine_effect_from_policy(
            self.state,
            self.bundle,
            self.scope,
            self.config,
            recovery=self.controller,
            pipeline_deps=self.deps,
        )
        assert isinstance(effect, InvokeAgentEffect), type(effect).__name__
        return effect

    def invoke(self, effect: InvokeAgentEffect, mode: str) -> object:
        route = registered_runners()[AgentEntryPoint.MAIN_PHASE_LOOP]
        deps = AttemptRunnerDeps(self.clock, PRODUCTION_INVOKE, None, None, None)
        token = install_agent_effect_dependencies(
            agent_runner=self._stub(mode),
            recovery_controller=self.controller,
            pipeline_deps=self.deps,
        )
        try:
            return route(
                effect=effect,
                config=self.config,
                controller=self.controller,
                deps=deps,
                state=self.state,
                run_id=_RUN_ID,
                pipeline_deps=self.deps,
                workspace_scope=self.scope,
                display_context=self.display_context,
            )
        finally:
            reset_agent_effect_dependencies(token)

    def run_step(self, mode: str) -> object:
        token = install_agent_effect_dependencies(
            agent_runner=self._stub(mode),
            recovery_controller=self.controller,
            pipeline_deps=self.deps,
        )
        try:
            return runner_module.run_pipeline_step(
                state=self.state,
                policy_bundle=self.bundle,
                workspace_scope=self.scope,
                config=self.config,
                display=self.display,
                display_context=self.display_context,
                verbosity=Verbosity.QUIET,
                registry=self.registry,
                pipeline_subscriber=None,
                recovery_controller=self.controller,
                pipeline_deps=self.deps,
            )
        finally:
            reset_agent_effect_dependencies(token)

    def drive(self, mode: str, attempts: int) -> list[InvokeAgentEffect]:
        effects: list[InvokeAgentEffect] = []
        for _attempt in range(attempts):
            effect = self.dispatch()
            effects.append(effect)
            with suppress(AgentInactivityTimeoutError):
                self.invoke(effect, mode)
        return effects


@pytest.mark.parametrize("phase", _production_phases())
def test_main_phase_shared_cooldown_scenario_for_every_default_phase(
    phase: str,
    tmp_git_repo: Path,
) -> None:
    harness = _MainPhaseHarness(phase, tmp_git_repo)
    threshold = harness.controller.ledger.consecutive_limit
    expected_duration_ms = int(harness.controller.ledger.base_cooldown_seconds * 1000)

    assert threshold == AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT

    effects = harness.drive("unproductive", threshold - 1)

    assert [effect.agent_name for effect in effects] == [harness.primary] * (threshold - 1)
    assert harness.invoked == [harness.primary] * (threshold - 1)
    assert isinstance(harness.controller.agent_eligibility(phase, harness.primary), Eligible)

    lines = _capture_cooldown_lines(lambda: harness.drive("unproductive", 1))

    assert harness.invoked == [harness.primary] * threshold

    status = harness.controller.agent_eligibility(phase, harness.primary)
    assert isinstance(status, Cooldown)
    assert status.reason == _CONSECUTIVE_REASON
    assert status.until_ms == expected_duration_ms

    assert [
        line
        for line in lines
        if f"phase={phase}" in line
        and f"agent={harness.primary}" in line
        and f"reason={UnavailabilityReason.DEAD_AGENT_NO_PROGRESS}" in line
        and f"duration_ms={expected_duration_ms}" in line
        and f"expiry_ms={expected_duration_ms}" in line
    ] != []

    next_effect = harness.dispatch()

    assert next_effect.agent_name == _FALLBACK

    invoked_before = list(harness.invoked)
    guarded = harness.invoke(next_effect, "unproductive")

    assert harness.invoked == [*invoked_before, _FALLBACK]
    assert harness.primary not in harness.invoked[threshold:]
    assert getattr(guarded, "event", None) is PipelineEvent.AGENT_FAILURE

    cooled_effect = InvokeAgentEffect(
        agent_name=harness.primary,
        phase=phase,
        prompt_file="prompt.md",
    )
    invoked_before = list(harness.invoked)
    refused = harness.invoke(cooled_effect, "unproductive")

    assert harness.invoked == invoked_before
    assert getattr(refused, "event", None) is PipelineEvent.AGENT_FAILURE


@pytest.mark.parametrize("phase", _production_phases())
def test_main_phase_stalled_reported_success_is_charged_and_cooled(
    phase: str,
    tmp_git_repo: Path,
) -> None:
    harness = _MainPhaseHarness(phase, tmp_git_repo)
    threshold = harness.controller.ledger.consecutive_limit
    expected_duration_ms = int(harness.controller.ledger.base_cooldown_seconds * 1000)

    harness.drive("stalled_success", threshold)

    status = harness.controller.agent_eligibility(phase, harness.primary)
    assert isinstance(status, Cooldown)
    assert status.reason == _CONSECUTIVE_REASON
    assert status.until_ms == expected_duration_ms
    assert harness.controller.preferred_agent_index(phase, harness.agents).agent == _FALLBACK


@pytest.mark.parametrize("phase", _production_phases())
def test_main_phase_no_activity_uses_the_shared_no_output_reason_and_duration(
    phase: str,
    tmp_git_repo: Path,
) -> None:
    harness = _MainPhaseHarness(phase, tmp_git_repo)
    reason = UnavailabilityReason.NO_OUTPUT_AT_START
    expected_duration_ms = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[reason].base_backoff_ms

    harness.run_step("no_activity")

    status = harness.controller.agent_eligibility(phase, harness.primary)
    assert isinstance(status, Cooldown)
    assert status.reason == str(reason)
    assert status.until_ms == expected_duration_ms
    assert harness.controller.preferred_agent_index(phase, harness.agents).agent == _FALLBACK
