"""Shared black-box scenario harness for agent failure and recovery tests.

PRODUCT_CRITERIA.md §3 and §6 public seam:
- Scripted fake agent through public interfaces.
- MemoryWorkspace / tmp_path.
- FakeClock.
- Pipeline dependencies via make_test_pipeline_deps and make_resolver_controller.
- Assert ONLY observable behavior: invocation sequence, run end state, and logs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from loguru import logger

from ralph.agents.invoke import AgentInactivityTimeoutError, AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.phases.execution import record_workspace_snapshot
from ralph.pipeline import run_loop
from ralph.pipeline import runner as runner_module
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
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._resolver_controller import make_resolver_controller

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps
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
    artifacts: dict[str, str] | None = None


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
) -> None:
    """Apply simulated workspace and artifact actions and raise configured exceptions."""
    if result.custom_action is not None:
        result.custom_action(workspace)

    if result.artifacts:
        for name, content in result.artifacts.items():
            if name == "development_result":
                workspace.write(".agent/artifacts/development_result.md", content)
            elif name == "plan":
                workspace.write(".agent/artifacts/plan.md", content)
            else:
                workspace.write(f".agent/artifacts/{name}.md", content)

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

    if result.failure_category is not None:
        if result.failure_category == FailureCategory.ENVIRONMENTAL:
            raise ConnectionRefusedError(f"Simulated environmental connection refusal for {agent}")
        if result.failure_category == FailureCategory.AMBIGUOUS:
            raise RuntimeError(f"Simulated ambiguous failure for {agent}")
        if result.failure_category == FailureCategory.AGENT:
            raise AgentInactivityTimeoutError(agent, 60.0)

    if result.exit_code != 0:
        raise AgentInvocationError(
            agent, result.exit_code, f"Simulated process exit code {result.exit_code} for {agent}"
        )


def _run_scenario_loop(
    state: PipelineState,
    bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
    display: ParallelDisplay,
    display_context: DisplayContext,
    controller: RecoveryController,
    deps: PipelineDeps,
    loop_ctx: run_loop.LoopContext,
    *,
    max_iterations: int,
    stop_after_invocations: int | None,
    invocations: list[str],
    advance_clock_on_wait: bool,
    cooldown_durations: dict[str, list[float]],
    effective_clock: FakeClock,
    agents: list[str] | tuple[str, ...],
) -> tuple[PipelineState, int]:
    terminal_phases = {"complete", "failed_terminal"}
    exit_code = 0
    seen_cooldown_until: dict[str, set[int]] = {}
    for _iteration in range(1, max_iterations + 1):
        if stop_after_invocations is not None and len(invocations) >= stop_after_invocations:
            break
        if state.phase in terminal_phases:
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
            recovery_controller=controller,
            pipeline_deps=deps,
        )

        for ag in agents:
            stat = controller.ledger.status(state.phase, ag)
            if isinstance(stat, Cooldown) and stat.until_ms not in seen_cooldown_until.get(
                ag, set()
            ):
                seen_cooldown_until.setdefault(ag, set()).add(stat.until_ms)
                now_ms = int(effective_clock.monotonic() * 1000)
                dur = (stat.until_ms - now_ms) / 1000.0
                cooldown_durations.setdefault(ag, []).append(dur)

        if isinstance(step_result, int):
            exit_code = step_result
            break

        if stop_after_invocations is not None and len(invocations) >= stop_after_invocations:
            state = step_result
            break

        state = run_loop.handle_cooldown_delay(step_result, loop_ctx)

        if not advance_clock_on_wait and state.is_waiting_state:
            break

    if state.phase == "failed_terminal":
        exit_code = 1
    elif state.phase == "complete":
        exit_code = 0
    return state, exit_code


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
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": False, "max_same_agent_retries": 0}}
    )
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

    from ralph.phases.required_artifacts import resolve_phase_required_artifact

    deps = make_test_pipeline_deps(
        display_context,
        recovery_controller_factory=lambda _s, _b, _c: (effective_controller, 0),
        policy_bundle=bundle,
        registry_factory=lambda _cfg: _ScenarioRegistry(),
        artifact_requirements_resolver=resolve_phase_required_artifact,
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

    def fake_invoke_agent(
        agent_config: AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del prompt_file, options, _clock
        agent = agent_config.cmd
        invocations.append(agent)
        attempt_num = agent_attempts.get(agent, 0) + 1
        agent_attempts[agent] = attempt_num

        result = resolve_attempt_result(agent, "development", attempt_num)
        record_workspace_snapshot(workspace, "development")
        _apply_attempt_side_effects(result, agent, attempt_num, workspace)
        return iter([f"Output from {agent} attempt {attempt_num}\n"])

    state = initial_state or PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=list(agents), current_index=0, retries=0),
        },
        policy_entry_phase="development",
    )

    def sleep_fn(dur: float) -> None:
        if advance_clock_on_wait:
            effective_clock.advance(dur)
            if on_cooldown_advance is not None:
                on_cooldown_advance(effective_clock, dur)

    loop_ctx = run_loop.LoopContext(
        policy_bundle=bundle,
        workspace_scope=workspace_scope,
        config=config,
        active_display=display,
        display_context=display_context,
        effective_verbosity=Verbosity.QUIET,
        registry=_ScenarioRegistry(),
        effective_pipeline_subscriber=None,
        controller=effective_controller,
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=FakeConnectivityMonitor(),
        sleep=sleep_fn,
        is_quiet=True,
    )

    sink_id = logger.add(log_sink, level="DEBUG")

    try:
        with (
            patch("ralph.pipeline.effect_executor.invoke_agent", side_effect=fake_invoke_agent),
            patch("ralph.agents.invoke.invoke_agent", side_effect=fake_invoke_agent),
        ):
            state, exit_code = _run_scenario_loop(
                state=state,
                bundle=bundle,
                workspace_scope=workspace_scope,
                config=config,
                display=display,
                display_context=display_context,
                controller=effective_controller,
                deps=deps,
                loop_ctx=loop_ctx,
                max_iterations=max_iterations,
                stop_after_invocations=stop_after_invocations,
                invocations=invocations,
                advance_clock_on_wait=advance_clock_on_wait,
                cooldown_durations=cooldown_durations,
                effective_clock=effective_clock,
                agents=agents,
            )

        return ScenarioOutcome(
            invocations=invocations,
            final_state=state,
            logs=captured_logs,
            exit_code=exit_code,
            clock=effective_clock,
            workspace_root=tmp_path,
            cooldown_durations=cooldown_durations,
        )
    finally:
        logger.remove(sink_id)
