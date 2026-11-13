from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.invoke import AgentInactivityTimeoutError, AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.mcp.artifacts.development_result_validation_error import (
    DevelopmentResultValidationError,
)
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
from ralph.recovery.canary_probe import CANARY_PROMPT_FILENAME
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.probe import DefaultWorkspaceChangeProbe
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from ralph.agents.invoke import InvokeOptions
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps

SCENARIO_PHASE = "development"


@dataclass(frozen=True)
class AgentAttemptResult:
    produce_artifact: bool = False
    modify_workspace: bool = False
    exit_code: int = 0
    failure_category: FailureCategory | None = None
    raise_error: Exception | None = None
    custom_action: Callable[[FsWorkspace], None] | None = None
    artifacts: dict[str, str] | None = None
    output_lines: tuple[str, ...] = ()


ScriptedAgentFn = Callable[[str, str, int, FsWorkspace], AgentAttemptResult]
ScriptedAgent = ScriptedAgentFn | dict[tuple[str, int], AgentAttemptResult]


@dataclass(frozen=True)
class ScenarioOutcome:
    invocations: list[str]
    final_state: PipelineState
    logs: list[str]
    exit_code: int
    clock: FakeClock
    workspace_root: Path
    cooldown_durations: dict[str, list[float]] = field(default_factory=dict)
    agent_outputs: list[str] = field(default_factory=list)
    probe_invocations: list[str] = field(default_factory=list)


class _ScenarioRegistry:
    def get(self, name: str) -> AgentConfig:
        return AgentConfig(cmd=name)


def _seed_valid_plan(workspace: FsWorkspace) -> None:
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


def _resolve_scripted_result(
    script: ScriptedAgent,
    agent: str,
    attempt_num: int,
    workspace: FsWorkspace,
) -> AgentAttemptResult:
    if callable(script):
        return script(agent, SCENARIO_PHASE, attempt_num, workspace)
    return script.get((agent, attempt_num), AgentAttemptResult())


def _raise_scripted_failure(result: AgentAttemptResult, agent: str) -> None:
    if result.raise_error is not None:
        raise result.raise_error
    if result.failure_category is not None:
        _raise_configured_failure(result.failure_category, agent)
    if result.exit_code != 0:
        raise AgentInvocationError(
            agent, result.exit_code, f"Simulated process exit code {result.exit_code} for {agent}"
        )


def _scripted_probe_attempt(
    script: ScriptedAgent,
    agent: str,
    workspace: FsWorkspace,
    probe_attempts: dict[str, int],
    probe_invocations: list[str],
) -> Iterator[str]:
    probe_invocations.append(agent)
    probe_num = probe_attempts.get(agent, 0) + 1
    probe_attempts[agent] = probe_num
    result = _resolve_scripted_result(script, agent, probe_num, workspace)
    _raise_scripted_failure(result, agent)
    return iter(result.output_lines or (f"Probe output from {agent} probe {probe_num}",))


def _raise_configured_failure(category: FailureCategory, agent: str) -> None:
    if category == FailureCategory.ENVIRONMENTAL:
        raise ConnectionRefusedError(f"Simulated environmental connection refusal for {agent}")
    if category == FailureCategory.AMBIGUOUS:
        raise RuntimeError(f"Simulated ambiguous failure for {agent}")
    if category == FailureCategory.AGENT:
        raise AgentInactivityTimeoutError(agent, 60.0)
    if category == FailureCategory.ARTIFACT_VALIDATION:
        raise DevelopmentResultValidationError(
            f"Simulated artifact validation failure for {agent}: the required "
            "development_result artifact was rejected"
        )


def _apply_attempt_side_effects(
    result: AgentAttemptResult,
    agent: str,
    attempt_num: int,
    workspace: FsWorkspace,
) -> None:
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

    if result.modify_workspace:
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

    category = result.failure_category
    if category is not None:
        _raise_configured_failure(category, agent)

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

    probe = DefaultWorkspaceChangeProbe()

    effective_controller = (
        controller
        if controller is not None
        else RecoveryController(
            options=RecoveryControllerOptions(
                clock=effective_clock,
                workspace_probe=probe,
            )
        )
    )

    from ralph.phases.required_artifacts import resolve_phase_required_artifact

    invocations: list[str] = []
    agent_attempts: dict[str, int] = {}
    probe_invocations: list[str] = []
    probe_attempts: dict[str, int] = {}
    cooldown_durations: dict[str, list[float]] = {}
    captured_logs: list[str] = []
    agent_outputs: list[str] = []

    def log_sink(msg: object) -> None:
        captured_logs.append(str(msg))

    def iter_attempt_output(
        agent: str,
        attempt_num: int,
        result: AgentAttemptResult,
    ) -> Iterator[str]:
        probe.observe_before(SCENARIO_PHASE, workspace)
        record_workspace_snapshot(workspace, SCENARIO_PHASE)
        lines = result.output_lines or (f"Output from {agent} attempt {attempt_num}",)
        for line in lines:
            agent_outputs.append(line)
            yield line
        _apply_attempt_side_effects(result, agent, attempt_num, workspace)

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del options, _clock
        agent = config.cmd
        if Path(prompt_file).name == CANARY_PROMPT_FILENAME:
            return _scripted_probe_attempt(
                script, agent, workspace, probe_attempts, probe_invocations
            )
        invocations.append(agent)
        attempt_num = agent_attempts.get(agent, 0) + 1
        agent_attempts[agent] = attempt_num

        result = _resolve_scripted_result(script, agent, attempt_num, workspace)
        return iter_attempt_output(agent, attempt_num, result)

    deps = make_test_pipeline_deps(
        display_context,
        recovery_controller_factory=lambda _s, _b, _c: (effective_controller, 0),
        policy_bundle=bundle,
        registry_factory=lambda _cfg: _ScenarioRegistry(),
        artifact_requirements_resolver=resolve_phase_required_artifact,
    )
    deps = replace(deps, invoke_agent=fake_invoke_agent)

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
            agent_outputs=agent_outputs,
            probe_invocations=probe_invocations,
        )
    finally:
        logger.remove(sink_id)
