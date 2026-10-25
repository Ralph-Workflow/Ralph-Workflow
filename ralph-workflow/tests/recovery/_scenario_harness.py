"""Shared black-box scenario harness for agent failure and recovery tests.

PRODUCT_CRITERIA.md §3 and §6 public seam:
- Scripted fake agent through public interfaces.
- MemoryWorkspace / tmp_path.
- FakeClock.
- Pipeline dependencies via make_test_pipeline_deps and make_resolver_controller.
- Assert ONLY observable behavior: invocation sequence, run end state, and logs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import Effect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.recovery.failure_category import FailureCategory
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._resolver_controller import make_resolver_controller

if TYPE_CHECKING:
    from ralph.recovery.controller import RecoveryController


@dataclass(frozen=True)
class AgentAttemptResult:
    """Configured outcome for a single agent invocation in a scenario."""

    produce_artifact: bool = False
    workspace_changed: bool = False
    exit_code: int = 0
    failure_category: FailureCategory | None = None
    raise_error: Exception | None = None
    custom_action: Callable[[FsWorkspace], None] | None = None


ScriptedAgentFn = Callable[[str, str, int, FsWorkspace], AgentAttemptResult]
ScriptedAgent = ScriptedAgentFn | dict[tuple[str, int], AgentAttemptResult]


@dataclass(frozen=True)
class ScenarioOutcome:
    """Observable outcome of a scenario execution run."""

    invocations: list[str]
    final_state: PipelineState
    logs: list[str]
    exit_code: int
    clock: FakeClock
    workspace_root: Path
    cooldown_durations: dict[str, list[float]] = field(default_factory=dict)


class _ScenarioRegistry:
    """In-memory agent registry returning basic configs for test agents."""

    def get(self, name: str) -> AgentConfig:
        return AgentConfig(cmd=name)


def _seed_valid_plan(workspace: FsWorkspace) -> None:
    """Seed minimal valid plan documents so execution phase validation proceeds."""
    plan_content = (
        "---\n"
        "type: plan\n"
        "---\n"
        "# Plan\n"
        "1. This is a complete test plan with more than ten words in order to satisfy the validator.\n"
    )
    workspace.write(".agent/artifacts/plan.md", plan_content)
    workspace.write(".agent/PLAN.md", plan_content)


def _build_policy_bundle(agents: list[str] | tuple[str, ...]) -> PolicyBundle:
    """Construct policy bundle with development execution phase and complete terminal phase."""
    agent_chains = {"dev": AgentChainConfig(agents=list(agents))}
    agent_drains = {"development": AgentDrainConfig(chain="dev")}
    agents_policy = AgentsPolicy(agent_chains=agent_chains, agent_drains=agent_drains)

    phases = {
        "development": PhaseDefinition(
            drain="development",
            role="execution",
            artifact_required=True,
            transitions=PhaseTransition(on_success="complete"),
        ),
        "complete": PhaseDefinition(
            drain="development",
            role="terminal",
            transitions=PhaseTransition(on_success="complete"),
        ),
        "failed_terminal": PhaseDefinition(
            drain="development",
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success="failed_terminal"),
        ),
    }
    pipeline_policy = PipelinePolicy(
        phases=phases,
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )

    artifacts = {
        "plan": ArtifactContract(
            drain="development",
            artifact_type="plan",
            artifact_path=".agent/artifacts/plan.md",
        ),
        "development_result": ArtifactContract(
            drain="development",
            artifact_type="development_result",
            artifact_path=".agent/artifacts/development_result.md",
        ),
    }
    artifacts_policy = ArtifactsPolicy(artifacts=artifacts)
    return PolicyBundle(agents=agents_policy, pipeline=pipeline_policy, artifacts=artifacts_policy)


def _apply_attempt_side_effects(
    result: AgentAttemptResult,
    agent: str,
    attempt_num: int,
    workspace: FsWorkspace,
) -> PipelineEvent:
    """Apply simulated workspace and artifact actions and return pipeline event."""
    if result.custom_action is not None:
        result.custom_action(workspace)

    if result.workspace_changed:
        workspace.write(
            f"workspace_changed_{agent}_{attempt_num}.txt",
            f"Modification by {agent} at attempt {attempt_num}\n",
        )

    if result.produce_artifact:
        workspace.write(
            ".agent/artifacts/development_result.md",
            "---\n"
            "type: development_result\n"
            "status: completed\n"
            "---\n"
            "# Development Result\n"
            f"Completed by {agent}.\n",
        )

    if result.raise_error is not None:
        raise result.raise_error

    if result.failure_category == FailureCategory.ENVIRONMENTAL:
        raise OSError(f"Simulated environmental network failure for {agent}")

    if result.failure_category == FailureCategory.AMBIGUOUS:
        raise RuntimeError(f"Simulated ambiguous failure for {agent}")

    if result.failure_category == FailureCategory.AGENT:
        raise AgentInactivityTimeoutError(agent, 15.0)

    # In Scenario 8: Exit 1 with valid artifact is success; exit 0 with no artifact is unproductive.
    if result.exit_code != 0 and not result.produce_artifact:
        return PipelineEvent.AGENT_FAILURE

    return PipelineEvent.AGENT_SUCCESS


def _reselect_preferred_or_wait(
    state: PipelineState,
    controller: RecoveryController,
    clock: FakeClock,
    cooldown_durations: dict[str, list[float]],
    *,
    advance_clock_on_wait: bool,
    on_cooldown_advance: Callable[[FakeClock, float], None] | None,
) -> tuple[PipelineState, bool]:
    """Check preferred agent selection and handle waiting state or exhaustion."""
    chain = state.chain_for_phase(state.phase)
    if chain is None or not hasattr(controller, "preferred_agent_index"):
        return state, False

    selection = controller.preferred_agent_index(str(state.phase), chain.agents)
    if selection.index is not None:
        if selection.index != chain.current_index or state.is_waiting_state:
            new_chain = AgentChainState(
                agents=chain.agents, current_index=selection.index, retries=0
            )
            state = state.with_phase_chain(state.phase, new_chain).copy_with(
                is_waiting_state=False, last_agent_session_id=None
            )
        return state, False

    # All agents unavailable / in cooldown
    delay_ms = state.last_retry_delay_ms or controller.earliest_available_wait_ms(
        str(state.phase), chain.agents
    )
    delay_seconds = (delay_ms or 5000) / 1000.0

    if chain.agents:
        last_agent = chain.agents[chain.current_index]
        cooldown_durations.setdefault(last_agent, []).append(delay_seconds)

    if not controller.agents_now_available(str(state.phase), chain.agents) and (
        delay_ms is None or delay_ms <= 0
    ):
        state = state.copy_with(
            phase="failed_terminal",
            last_error="All agents exhausted in chain",
        )
        return state, True

    if advance_clock_on_wait:
        clock.advance(delay_seconds)
        if on_cooldown_advance is not None:
            on_cooldown_advance(clock, delay_seconds)
        state = state.copy_with(is_waiting_state=False)

    return state, False


def run_scenario(
    tmp_path: Path,
    script: ScriptedAgent,
    *,
    agents: list[str] | tuple[str, ...] = ("agent_a", "agent_b"),
    clock: FakeClock | None = None,
    controller: RecoveryController | None = None,
    max_iterations: int = 25,
    stop_after_invocations: int | None = None,
    initial_state: PipelineState | None = None,
    seed_plan: bool = True,
    advance_clock_on_wait: bool = True,
    on_cooldown_advance: Callable[[FakeClock, float], None] | None = None,
) -> ScenarioOutcome:
    """Execute a scenario run through the public pipeline seam with fake clock and scripted agent."""
    effective_clock = clock if clock is not None else FakeClock(start=0.0)
    workspace = FsWorkspace(tmp_path)
    workspace_scope = WorkspaceScope(tmp_path)
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}})
    display_context = make_display_context()
    display = ParallelDisplay(
        workspace_root=tmp_path, display_context=display_context, is_quiet=True
    )

    if seed_plan:
        _seed_valid_plan(workspace)

    bundle = _build_policy_bundle(agents)
    runner_module.register_role_handlers(bundle.pipeline)

    effective_controller = (
        controller if controller is not None else make_resolver_controller(effective_clock)
    )

    deps = make_test_pipeline_deps(
        display_context,
        recovery_controller_factory=lambda _s, _b, _c: (effective_controller, 0),
        policy_bundle=bundle,
    )

    invocations: list[str] = []
    agent_attempts: dict[str, int] = {}
    cooldown_durations: dict[str, list[float]] = {}
    captured_logs: list[str] = []

    def log_sink(msg: object) -> None:
        captured_logs.append(str(msg))

    def resolve_attempt_result(agent: str, phase: str, attempt_num: int) -> AgentAttemptResult:
        if callable(script):
            return script(agent, phase, attempt_num, workspace)
        return script.get((agent, attempt_num), AgentAttemptResult())

    def fake_execute_effect(
        effect: Effect,
        *args: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del args, kwargs
        if not isinstance(effect, InvokeAgentEffect):
            return PipelineEvent.AGENT_SUCCESS

        agent = effect.agent_name
        invocations.append(agent)
        attempt_num = agent_attempts.get(agent, 0) + 1
        agent_attempts[agent] = attempt_num

        result = resolve_attempt_result(agent, effect.phase, attempt_num)
        return _apply_attempt_side_effects(result, agent, attempt_num, workspace)

    state = initial_state or PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=list(agents), current_index=0, retries=0),
        },
        policy_entry_phase="development",
    )

    original_execute_effect = runner_module.execute_effect
    runner_module.execute_effect = fake_execute_effect
    sink_id = logger.add(log_sink, level="DEBUG")

    terminal_phases = {"complete", "failed_terminal"}
    exit_code = 0

    try:
        for _iteration in range(1, max_iterations + 1):
            if stop_after_invocations is not None and len(invocations) >= stop_after_invocations:
                break
            if state.phase in terminal_phases:
                break

            state, should_terminate = _reselect_preferred_or_wait(
                state,
                effective_controller,
                effective_clock,
                cooldown_durations,
                advance_clock_on_wait=advance_clock_on_wait,
                on_cooldown_advance=on_cooldown_advance,
            )
            if should_terminate:
                exit_code = 1
                break

            step_result = runner_module.run_pipeline_step(
                state=state,
                policy_bundle=bundle,
                workspace_scope=workspace_scope,
                config=config,
                display=display,
                display_context=display_context,
                verbosity=Verbosity.QUIET,
                registry=_ScenarioRegistry(),
                pipeline_subscriber=None,
                recovery_controller=effective_controller,
                pipeline_deps=deps,
            )

            if isinstance(step_result, int):
                exit_code = step_result
                break

            state = step_result
            if state.phase == "failed_terminal":
                exit_code = 1
    finally:
        logger.remove(sink_id)
        runner_module.execute_effect = original_execute_effect

    return ScenarioOutcome(
        invocations=invocations,
        final_state=state,
        logs=captured_logs,
        exit_code=exit_code,
        clock=effective_clock,
        workspace_root=tmp_path,
        cooldown_durations=cooldown_durations,
    )
