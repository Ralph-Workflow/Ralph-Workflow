"""Main pipeline runner and effect handlers.

This module implements the event loop that drives the pipeline:
determine_effect(state) -> Effect -> Handler -> Event -> reduce(state, event) -> new_state

The runner coordinates between the orchestrator (pure effect determination),
the handlers (I/O execution), and the reducer (state transitions).
"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from inspect import signature
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from git import InvalidGitRepositoryError, Repo
from loguru import logger
from rich.console import Console
from rich.text import Text

from ralph.agents.chain import ChainManager
from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_FAILED,
    PHASE_PLANNING,
    Verbosity,
)
from ralph.display.phase_banner import show_phase_start, show_phase_transition
from ralph.mcp.capability_mapping import DrainClass, drain_class_for_session
from ralph.mcp.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    read_commit_message_from_path,
)
from ralph.mcp.server.lifecycle import shutdown_mcp_server, start_mcp_server
from ralph.mcp.session import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV, AgentSession
from ralph.phases import PhaseContext, handle_phase
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    ExitFailureEffect,
    ExitSuccessEffect,
    FanOutDevelopmentEffect,
    InvokeAgentEffect,
    MergeIntegrationEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RebaseState
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.loader import load_policy_or_die
from ralph.prompts.materialize import (
    materialize_prompt_for_phase,
    prompt_file_for_phase,
    tool_name_prefix_for_transport,
)
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace import FsWorkspace
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.agents.executor import AgentExecutor
    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.display.subscriber import DashboardSubscriber
    from ralph.policy.models import AgentsPolicy, PhaseDefinition, PipelinePolicy, PolicyBundle

    class _DashboardSubscriber(Protocol):
        def notify(self, state: PipelineState) -> None: ...


class _InvokeAgentFn(Protocol):
    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterable[object]: ...


class _RegistryLike(Protocol):
    def get(self, name: str) -> AgentConfig | None: ...


class _AgentRegistryFactory(Protocol):
    @classmethod
    def from_config(cls, config: UnifiedConfig) -> _RegistryLike: ...


console = Console()
_VERBOSE_LOG_LEVEL = 2
_AGENT_ACTIVITY_LOG_LEVEL = 1
_MAX_METADATA_PARTS = 3
_MAX_TEXT_LENGTH = 200
_MAX_TOOL_INPUT_LENGTH = 120
_MAX_TOOL_RESULT_LENGTH = 150
_MAX_TOOL_RESULT_BRIEF = 80
_TOOL_RESULT_BRIEF_THRESHOLD = 500
_MAX_METADATA_SUMMARY_LENGTH = 120
_LEGACY_EXECUTE_EFFECT_ARITY = 3
_EVENT_DECISION_LABELS: dict[PipelineEvent, str] = {
    PipelineEvent.ANALYSIS_SUCCESS: "approved",
    PipelineEvent.ANALYSIS_LOOPBACK: "needs changes",
    PipelineEvent.REVIEW_CLEAN: "clean — no issues",
    PipelineEvent.REVIEW_ISSUES_FOUND: "issues found",
    PipelineEvent.COMMIT_SUCCESS: "committed",
    PipelineEvent.COMMIT_SKIPPED: "skipped — nothing to commit",
    PipelineEvent.FIX_SUCCESS: "fixed",
}

_VERBOSITY_RANK: dict[Verbosity, int] = {
    Verbosity.QUIET: 0,
    Verbosity.NORMAL: 1,
    Verbosity.VERBOSE: 2,
    Verbosity.FULL: 3,
    Verbosity.DEBUG: 4,
}


def _verbosity_rank(verbosity: Verbosity) -> int:
    """Return a numeric rank for a Verbosity enum value (QUIET=0 .. DEBUG=4)."""
    return _VERBOSITY_RANK.get(verbosity, _VERBOSITY_RANK[Verbosity.VERBOSE])


def _normalize_verbosity(value: Verbosity | int | None) -> Verbosity:
    """Coerce a Verbosity enum, integer rank, or None into a Verbosity value.

    The legacy ``GeneralConfig.verbosity`` field is an integer (0-4); the new
    CLI surface is the ``Verbosity`` StrEnum. This helper accepts either and
    falls back to ``Verbosity.VERBOSE`` for unknown / unset inputs.
    """
    if isinstance(value, Verbosity):
        return value
    if isinstance(value, int):
        for vb, rank in _VERBOSITY_RANK.items():
            if rank == value:
                return vb
    return Verbosity.VERBOSE


def _terminal_width() -> int:
    """Return the current terminal width with a safe fallback."""
    return shutil.get_terminal_size().columns or 80


def _available_width(prefix_len: int) -> int:
    """Return available width for content after a prefix, with a floor of 40."""
    return max(40, _terminal_width() - prefix_len - 2)


@dataclass(frozen=True)
class _AgentExecutionDeps:
    invoke_agent: _InvokeAgentFn
    agent_invocation_error: type[Exception]
    agent_registry: _AgentRegistryFactory


def _write_start_commit_if_absent(workspace_root: Path) -> None:
    start_commit_path = workspace_root / ".agent" / "start_commit"
    if start_commit_path.exists():
        return
    try:
        repo = Repo(workspace_root)
    except InvalidGitRepositoryError:
        return
    if not repo.head.is_valid():
        return
    start_commit_path.parent.mkdir(parents=True, exist_ok=True)
    start_commit_path.write_text(repo.head.commit.hexsha + "\n")


class _LegacyConsoleDisplay:
    def __enter__(self) -> _LegacyConsoleDisplay:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        return None

    def emit(self, unit_id: str | None, line: Text | str) -> None:
        if unit_id is None:
            console.print(line)
            return
        console.print(f"[{unit_id}] {line}")


def _emit_display_line(
    display: ParallelDisplay | _LegacyConsoleDisplay | None,
    unit_id: str | None,
    line: Text | str,
) -> None:
    if display is None:
        if unit_id is None:
            console.print(line)
            return
        console.print(f"[{unit_id}] {line}")
        return
    if isinstance(display, _LegacyConsoleDisplay):
        display.emit(unit_id, line)
        return
    display.emit(unit_id, line.plain if isinstance(line, Text) else line)


def _resolve_display(
    display: ParallelDisplay | None,
) -> ParallelDisplay | _LegacyConsoleDisplay:
    if display is not None:
        return display
    return _LegacyConsoleDisplay()


def _build_default_display(
    workspace_root: Path,
) -> ParallelDisplay | _LegacyConsoleDisplay:
    """Construct the default ParallelDisplay for the verbose run path.

    Falls back to the legacy console display if ParallelDisplay (or its
    transitive Rich/panel dependencies) cannot be imported. The display
    owns its own DashboardSubscriber so the runner and the live render
    thread share a single subscriber.
    """
    try:
        from ralph.display.parallel_display import (  # noqa: PLC0415
            ParallelDisplay as _ParallelDisplay,
        )
    except ImportError:
        logger.debug("ParallelDisplay unavailable; falling back to legacy console")
        return _LegacyConsoleDisplay()

    return _ParallelDisplay(
        console=console,
        env=dict(os.environ),
        workspace_root=workspace_root,
        run_id=str(uuid.uuid4()),
    )


def _execute_effect_with_optional_display(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | _LegacyConsoleDisplay,
    *,
    verbosity: Verbosity = Verbosity.VERBOSE,
) -> Event:
    params = signature(_execute_effect).parameters
    if len(params) == _LEGACY_EXECUTE_EFFECT_ARITY:
        return _execute_effect(effect, config, workspace_scope)
    if "verbosity" in params:
        return _execute_effect(effect, config, workspace_scope, display, verbosity=verbosity)
    return _execute_effect(effect, config, workspace_scope, display)


def _notify_dashboard_subscriber(
    dashboard_subscriber: _DashboardSubscriber | None,
    state: PipelineState,
) -> None:
    if dashboard_subscriber is None:
        return
    dashboard_subscriber.notify(state)


def _phase_context(state: PipelineState, previous_phase: str) -> dict[str, object]:
    """Build a context dict for emit_phase_transition with iteration/decision hints."""
    context: dict[str, object] = {}
    if state.phase in {"development", "fix"}:
        context["iteration"] = f"{state.iteration + 1}/{state.total_iterations}"
    if state.phase == "review":
        context["pass"] = f"{state.reviewer_pass + 1}/{state.total_reviewer_passes}"
    if previous_phase in {"development_analysis", "review_analysis"}:
        if state.phase in {"development_commit", "review_commit"}:
            context["decision"] = "approved"
        elif state.phase in {"development", "fix"}:
            context["decision"] = "needs changes"
    if previous_phase == "development_commit":
        context["dev_budget"] = f"{state.total_iterations - state.iteration} remaining"
    if previous_phase == "review_commit":
        context["review_budget"] = f"{state.total_reviewer_passes - state.reviewer_pass} remaining"
    return context


def _emit_phase_transition_if_changed(
    display: ParallelDisplay | _LegacyConsoleDisplay,
    previous_phase: str,
    state: PipelineState,
    *,
    verbosity: Verbosity,
) -> str:
    """Emit a phase-transition banner if state.phase != previous_phase.

    Returns the new previous_phase value (always state.phase). Quiet mode
    is a no-op except for state tracking.
    """
    if state.phase == previous_phase:
        return previous_phase
    if _verbosity_rank(verbosity) <= _VERBOSITY_RANK[Verbosity.QUIET]:
        return state.phase

    context = _phase_context(state, previous_phase) or None
    if hasattr(display, "emit_phase_transition"):
        try:
            display.emit_phase_transition(previous_phase, state.phase, context=context)
        except Exception:  # pragma: no cover - defensive
            logger.debug("display.emit_phase_transition failed", exc_info=True)
    else:
        try:
            show_phase_transition(previous_phase, state.phase, context=context, console=console)
        except Exception:  # pragma: no cover - defensive
            logger.debug("show_phase_transition failed", exc_info=True)
    return state.phase


def _emit_final_summary(
    state: PipelineState,
    workspace_root: Path,
    *,
    subscriber: DashboardSubscriber | None = None,
) -> None:
    """Emit an end-of-run completion summary panel.

    Called unconditionally after the pipeline loop exits (including via
    exception) so the user sees a final summary of what Ralph did, what
    was decided, and whether verification passed.

    When a ``subscriber`` is supplied, the snapshot is built from its
    accumulated state (decision log, analysis, plan) so the panel mirrors
    what the live dashboard showed during the run.
    """
    try:
        from ralph.display.completion_summary import emit_completion_summary  # noqa: PLC0415
        from ralph.display.snapshot import snapshot_from_state  # noqa: PLC0415

        snapshot = None
        if subscriber is not None:
            try:
                snapshot = subscriber.build_snapshot(state)
            except Exception:
                logger.debug(
                    "subscriber.build_snapshot failed; falling back to raw snapshot",
                    exc_info=True,
                )
        if snapshot is None:
            snapshot = snapshot_from_state(
                state,
                prompt_path=None,
                prompt_preview=(),
                run_id=None,
            )
        emit_completion_summary(console, snapshot, workspace_root=workspace_root)
    except Exception:
        logger.debug("Failed to emit completion summary", exc_info=True)


def run(  # noqa: PLR0912, PLR0915
    config: UnifiedConfig,
    initial_state: PipelineState | None = None,
    display: ParallelDisplay | None = None,
    dashboard_subscriber: _DashboardSubscriber | None = None,
    *,
    verbosity: Verbosity | None = None,
) -> int:
    """Execute the pipeline event loop.

    Args:
        config: Unified configuration for the pipeline.
        initial_state: Optional initial state (for resume from checkpoint).
        display: Optional pre-built display. When omitted, a ParallelDisplay
            is constructed by default unless ``verbosity`` is QUIET.
        dashboard_subscriber: Optional subscriber that will receive notify(state)
            calls after each reduce. When a ParallelDisplay is constructed by
            this function, its built-in subscriber is wired in automatically.
        verbosity: Optional explicit verbosity. Defaults to the configured
            value in ``config.general.verbosity`` (mapped from int rank).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    workspace_scope = resolve_workspace_scope()
    _write_start_commit_if_absent(workspace_scope.root)
    policy_bundle = load_policy_or_die(workspace_scope.root / ".agent")
    registry = AgentRegistry.from_config(config)
    state = initial_state or _create_initial_state(
        config,
        agents_policy=policy_bundle.agents,
        pipeline_policy=policy_bundle.pipeline,
    )

    effective_verbosity = _normalize_verbosity(
        verbosity if verbosity is not None else config.general.verbosity
    )
    is_quiet = _verbosity_rank(effective_verbosity) <= _VERBOSITY_RANK[Verbosity.QUIET]

    logger.info(
        "Starting pipeline: phase={}, iterations={}, reviews={}",
        state.phase,
        state.total_iterations,
        state.total_reviewer_passes,
    )

    if display is not None:
        active_display: ParallelDisplay | _LegacyConsoleDisplay = display
    elif is_quiet:
        active_display = _LegacyConsoleDisplay()
    else:
        active_display = _build_default_display(workspace_scope.root)

    if dashboard_subscriber is None and hasattr(active_display, "subscriber"):
        dashboard_subscriber = cast(
            "_DashboardSubscriber | None",
            getattr(active_display, "subscriber", None),
        )

    exit_code = 0
    _prev_phase = state.phase
    try:
        with active_display:
            try:
                while state.phase not in (PHASE_COMPLETE, PHASE_FAILED):
                    effect = _call_determine_effect_from_policy(
                        state, policy_bundle, workspace_scope
                    )
                    inline_result = _handle_inline_effect(
                        effect=effect,
                        state=state,
                        pipeline_policy=policy_bundle.pipeline,
                        workspace_scope=workspace_scope,
                        display=active_display,
                        dashboard_subscriber=dashboard_subscriber,
                    )
                    if inline_result is not None:
                        if isinstance(inline_result, int):
                            return inline_result
                        state = inline_result
                        _prev_phase = _emit_phase_transition_if_changed(
                            active_display,
                            _prev_phase,
                            state,
                            verbosity=effective_verbosity,
                        )
                        continue

                    if isinstance(effect, FanOutDevelopmentEffect):
                        state = _execute_fan_out_sync(
                            effect=effect,
                            state=state,
                            display=active_display,
                            policy_bundle=policy_bundle,
                            workspace_scope=workspace_scope,
                            dashboard_subscriber=dashboard_subscriber,
                        )
                        _prev_phase = _emit_phase_transition_if_changed(
                            active_display,
                            _prev_phase,
                            state,
                            verbosity=effective_verbosity,
                        )
                        continue

                    workspace = FsWorkspace(
                        workspace_scope.root,
                        allowed_roots=workspace_scope.allowed_roots,
                    )
                    _materialize_agent_prompt_if_needed(
                        effect,
                        workspace,
                        policy_bundle.pipeline,
                        registry,
                        workspace_scope,
                    )

                    event: Event = _execute_effect_with_optional_display(
                        effect,
                        config,
                        workspace_scope,
                        active_display,
                        verbosity=effective_verbosity,
                    )
                    if (
                        isinstance(effect, InvokeAgentEffect)
                        and event == PipelineEvent.AGENT_SUCCESS
                    ):
                        event = _phase_event_after_agent_run(
                            effect=effect,
                            config=config,
                            policy_bundle=policy_bundle,
                            workspace=workspace,
                            workspace_scope=workspace_scope,
                            display=active_display,
                        )

                    state, _ = reducer_reduce(state, event, policy_bundle.pipeline)
                    _notify_dashboard_subscriber(dashboard_subscriber, state)
                    ckpt.save(state)
                    _prev_phase = _emit_phase_transition_if_changed(
                        active_display,
                        _prev_phase,
                        state,
                        verbosity=effective_verbosity,
                    )

            except KeyboardInterrupt:
                logger.warning("Interrupted by user; saving checkpoint.")
                interrupted_state = state.copy_with(interrupted_by_user=True)
                ckpt.save(interrupted_state)
                return 130

            if state.phase == PHASE_COMPLETE:
                active_display.emit(None, "[green]Pipeline completed successfully.[/green]")
                exit_code = 0
            else:
                _emit_display_line(
                    active_display,
                    None,
                    _status_text("Pipeline failed", state.last_error or "Unknown error", "red"),
                )
                exit_code = 1
    finally:
        _emit_final_summary(
            state,
            workspace_scope.root,
            subscriber=cast("DashboardSubscriber | None", dashboard_subscriber),
        )
    return exit_code


def _execute_fan_out_sync(  # noqa: PLR0913
    *,
    effect: FanOutDevelopmentEffect,
    state: PipelineState,
    display: ParallelDisplay | _LegacyConsoleDisplay,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    dashboard_subscriber: _DashboardSubscriber | None = None,
) -> PipelineState:
    """Execute fan-out development synchronously by wrapping asyncio.run()."""
    import asyncio  # noqa: PLC0415

    from ralph.agents.subprocess_executor import SubprocessAgentExecutor  # noqa: PLC0415
    from ralph.display.parallel_display import ParallelDisplay as _ParallelDisplay  # noqa: PLC0415
    from ralph.git.executor import GitExecutor  # noqa: PLC0415
    from ralph.git.worktree_manager import WorktreeManager  # noqa: PLC0415
    from ralph.interrupt.asyncio_bridge import (  # noqa: PLC0415
        SignalBridge,
        install_signal_handlers,
    )
    from ralph.mcp.server.factory_impl import DynamicBindingMcpServerFactory  # noqa: PLC0415
    from ralph.pipeline.parallel import coordinator, merge_integrator  # noqa: PLC0415

    git_exec = GitExecutor()
    repo_root = workspace_scope.root

    pd: _ParallelDisplay = (
        display if isinstance(display, _ParallelDisplay) else _ParallelDisplay(console)
    )
    effective_dashboard_subscriber = dashboard_subscriber
    if effective_dashboard_subscriber is None and hasattr(pd, "subscriber"):
        effective_dashboard_subscriber = cast(
            "_DashboardSubscriber | None",
            getattr(pd, "subscriber", None),
        )

    async def _run() -> PipelineState:
        loop = asyncio.get_running_loop()
        bridge = SignalBridge()
        root_task: asyncio.Task[object] | None = cast(
            "asyncio.Task[object] | None", asyncio.current_task()
        )
        assert root_task is not None
        install_signal_handlers(loop, root_task, bridge)

        executor = cast(
            "AgentExecutor",
            SubprocessAgentExecutor(_parallel_worker_command(), signal_bridge=bridge),
        )
        workspace = FsWorkspace(
            workspace_scope.root,
            allowed_roots=workspace_scope.allowed_roots,
        )
        isolation = coordinator._IsolationDeps(
            worktree_manager=WorktreeManager(repo_root, git_exec),
            mcp_factory=DynamicBindingMcpServerFactory(workspace=workspace),
            repo_root=repo_root,
            executor_command=_parallel_worker_command(),
            signal_bridge=bridge,
        )
        resumed_state, _ = reducer_reduce(
            state, PipelineEvent.WORKERS_RESUMED, policy_bundle.pipeline
        )
        _notify_dashboard_subscriber(effective_dashboard_subscriber, resumed_state)
        completed_ids = {
            uid
            for uid, ws in resumed_state.worker_states.items()
            if ws.status == WorkerStatus.SUCCEEDED
        }
        resume_units = tuple(u for u in effect.work_units if u.unit_id not in completed_ids)

        if not resume_units:
            return resumed_state

        resume_effect = FanOutDevelopmentEffect(
            work_units=resume_units,
            max_workers=effect.max_workers,
        )
        fan_out_events = await coordinator.run_fan_out(
            effect=resume_effect,
            executor=executor,
            display=pd,
            ctx=coordinator._WorkerContext(
                log=coordinator._WorkerLog(
                    log_dir=workspace_scope.root / ".agent" / "logs",
                    run_id=str(uuid.uuid4()),
                ),
                isolation=isolation,
            ),
        )
        current = resumed_state
        for ev in fan_out_events:
            current, _ = reducer_reduce(current, ev, policy_bundle.pipeline)
            _notify_dashboard_subscriber(effective_dashboard_subscriber, current)
        ckpt.save(current)

        merge_effect = MergeIntegrationEffect(
            worker_states=current.worker_states,
            base_branch="main",
        )
        merge_result = await merge_integrator.integrate(
            base_branch=merge_effect.base_branch,
            worker_states=merge_effect.worker_states,
            git_executor=git_exec,
            repo_root=repo_root,
        )
        for ev in merge_result.events:
            current, _ = reducer_reduce(current, ev, policy_bundle.pipeline)
            _notify_dashboard_subscriber(effective_dashboard_subscriber, current)
        ckpt.save(current)
        return current

    return asyncio.run(_run())


def _parallel_worker_command() -> tuple[str, ...]:
    return (sys.executable, "-m", "ralph")


def _handle_inline_effect(  # noqa: PLR0913
    *,
    effect: Effect,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    dashboard_subscriber: _DashboardSubscriber | None = None,
) -> PipelineState | int | None:
    if isinstance(effect, SaveCheckpointEffect):
        ckpt.save(state)
        new_state, _ = reducer_reduce(state, PipelineEvent.CHECKPOINT_SAVED, pipeline_policy)
        _notify_dashboard_subscriber(dashboard_subscriber, new_state)
        return new_state

    if isinstance(effect, PreparePromptEffect):
        _materialize_prepared_prompt(effect, pipeline_policy, workspace_scope)
        updated_state = state.copy_with(
            phase=effect.phase,
            iteration=effect.iteration,
            current_drain=effect.drain or resolve_phase_drain(effect.phase, pipeline_policy),
        )
        ckpt.save(updated_state)
        _notify_dashboard_subscriber(dashboard_subscriber, updated_state)
        return updated_state

    if isinstance(effect, ExitSuccessEffect):
        _emit_display_line(display, None, "[green]Pipeline completed successfully.[/green]")
        return 0

    if isinstance(effect, ExitFailureEffect):
        _emit_display_line(display, None, _status_text("Pipeline failed", effect.reason, "red"))
        return 1

    return None


def _materialize_prepared_prompt(
    effect: PreparePromptEffect,
    pipeline_policy: PipelinePolicy,
    workspace_scope: WorkspaceScope,
) -> None:
    workspace = FsWorkspace(
        workspace_scope.root,
        allowed_roots=workspace_scope.allowed_roots,
    )
    materialize_prompt_for_phase(
        phase=effect.phase,
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        session_caps=SessionCapabilities.defaults_for_drain(
            _prompt_session_drain_for_phase(
                effect.drain or resolve_phase_drain(effect.phase, pipeline_policy) or effect.phase
            )
        ),
        workspace_root=workspace_scope.root,
    )


def _materialize_agent_prompt_if_needed(
    effect: Effect,
    workspace: FsWorkspace,
    pipeline_policy: PipelinePolicy,
    registry: _RegistryLike,
    workspace_scope: WorkspaceScope,
) -> None:
    if not isinstance(effect, InvokeAgentEffect):
        return

    agent = registry.get(effect.agent_name)
    tool_name_prefix = ""
    if agent is not None:
        tool_name_prefix = tool_name_prefix_for_transport(agent.transport)

    materialize_prompt_for_phase(
        phase=effect.phase,
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        session_caps=SessionCapabilities.defaults_for_drain(
            _prompt_session_drain_for_phase(
                effect.drain or resolve_phase_drain(effect.phase, pipeline_policy) or effect.phase
            ),
            tool_name_prefix=tool_name_prefix,
        ),
        workspace_root=workspace_scope.root,
    )


def _create_initial_state(
    config: UnifiedConfig,
    *,
    agents_policy: AgentsPolicy | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> PipelineState:
    """Create initial pipeline state from configuration.

    Args:
        config: Unified configuration.

    Returns:
        Initial PipelineState.
    """
    # Set up agent chains from config
    planning_agents = _agents_for_phase(
        config,
        "planning",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    dev_agents = _agents_for_phase(
        config,
        "development",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    dev_analysis_agents = _agents_for_phase(
        config,
        "development_analysis",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    rev_agents = _agents_for_phase(
        config,
        "review",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    review_analysis_agents = _agents_for_phase(
        config,
        "review_analysis",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    fix_agents = _agents_for_phase(
        config,
        "fix",
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )
    entry_phase = pipeline_policy.entry_phase if pipeline_policy is not None else PHASE_PLANNING

    return PipelineState(
        phase=entry_phase,
        total_iterations=config.general.developer_iters,
        total_reviewer_passes=config.general.reviewer_reviews,
        development_budget_remaining=config.general.developer_iters,
        review_budget_remaining=config.general.reviewer_reviews,
        planning_chain=AgentChainState(agents=planning_agents),
        dev_chain=AgentChainState(agents=dev_agents),
        dev_analysis_chain=AgentChainState(agents=dev_analysis_agents),
        rev_chain=AgentChainState(agents=rev_agents),
        review_analysis_chain=AgentChainState(agents=review_analysis_agents),
        fix_chain=AgentChainState(agents=fix_agents),
        rebase=RebaseState(),
        commit=CommitState(),
        policy_entry_phase=entry_phase,
        current_drain=(
            resolve_phase_drain(entry_phase, pipeline_policy)
            if pipeline_policy is not None
            else None
        ),
    )


def _call_determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
) -> Effect:
    determine_effect = _determine_effect_from_policy
    params = signature(determine_effect).parameters.values()
    positional = [
        param
        for param in params
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    if (
        any(param.kind == param.VAR_POSITIONAL for param in params)
        or len(positional) >= _LEGACY_EXECUTE_EFFECT_ARITY
    ):
        return determine_effect(state, policy_bundle, workspace_scope)
    return determine_effect(state, policy_bundle)


def _terminal_phase_effect(state: PipelineState) -> Effect | None:
    if state.phase == PHASE_COMPLETE:
        return ExitSuccessEffect()
    if state.phase == PHASE_FAILED:
        return ExitFailureEffect(reason=state.last_error or "Unknown failure")
    return None


def _determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope | None = None,
) -> Effect:
    terminal = _terminal_phase_effect(state)
    if terminal is not None:
        return terminal

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return ExitFailureEffect(reason=f"Unknown phase: {state.phase}")

    if phase_def.requires_commit:
        scope = workspace_scope or resolve_workspace_scope()
        return _commit_phase_effect(state, policy_bundle, phase_def, scope)

    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for phase '{state.phase}'")

    if state.phase == PHASE_DEVELOPMENT and state.work_units:
        parallel_policy = policy_bundle.pipeline.parallel_execution
        return FanOutDevelopmentEffect(
            work_units=state.work_units,
            max_workers=parallel_policy.max_parallel_workers if parallel_policy is not None else 8,
        )

    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=state.phase,
        prompt_file=prompt_file_for_phase(state.phase),
        drain=phase_def.drain,
    )


def _commit_phase_effect(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    phase_def: PhaseDefinition,
    workspace_scope: WorkspaceScope,
) -> Effect:
    if state.commit.agent_invoked:
        return _commit_effect(workspace_scope.root)
    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for commit phase '{state.phase}'")
    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=state.phase,
        prompt_file=prompt_file_for_phase(state.phase),
        drain=phase_def.drain,
    )


def _agents_for_phase(
    config: UnifiedConfig,
    phase: str,
    *,
    agents_policy: AgentsPolicy | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> list[str]:
    if agents_policy is not None and pipeline_policy is not None:
        phase_def = pipeline_policy.phases.get(phase)
        if phase_def is not None:
            binding = agents_policy.agent_drains.get(phase_def.drain)
            if binding is not None:
                chain = agents_policy.agent_chains.get(binding.chain)
                if chain is not None:
                    return list(chain.agents)

    drains = config.agent_drains if isinstance(config.agent_drains, dict) else {}
    chains = config.agent_chains if isinstance(config.agent_chains, dict) else {}
    chain_name = drains.get(phase) or phase
    return list(chains.get(chain_name, []))


def _agent_name_for_phase_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
) -> str | None:
    current_agent = state.current_agent()
    if current_agent is not None:
        return current_agent

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return None

    binding = policy_bundle.agents.agent_drains.get(phase_def.drain)
    if binding is None:
        return None

    chain = policy_bundle.agents.agent_chains.get(binding.chain)
    if chain is None or not chain.agents:
        return None
    return chain.agents[0]


def _phase_event_after_agent_run(  # noqa: PLR0913
    *,
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    workspace: FsWorkspace,
    workspace_scope: WorkspaceScope | None = None,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
) -> Event:
    ctx = PhaseContext.model_construct(
        workspace=workspace,
        registry=AgentRegistry.from_config(config),
        chain_manager=ChainManager(policy_bundle.agents),
        pipeline_policy=policy_bundle.pipeline,
        agents_policy=policy_bundle.agents,
        artifacts_policy=policy_bundle.artifacts,
        config=config,
    )
    events = handle_phase(effect, ctx)
    event: Event = events[0] if events else PipelineEvent.AGENT_SUCCESS

    if (
        display is not None
        and workspace_scope is not None
        and event in (PipelineEvent.ANALYSIS_SUCCESS, PipelineEvent.ANALYSIS_LOOPBACK)
        and hasattr(display, "emit_analysis_result")
    ):
        try:
            from ralph.display.artifact_reader import (  # noqa: PLC0415
                read_latest_analysis_decision,
            )

            drain = effect.drain or effect.phase
            summary = read_latest_analysis_decision(workspace_scope.root, drain)
            if summary is not None:
                display.emit_analysis_result(
                    phase=effect.phase,
                    decision=summary.decision,
                    reason=summary.reason,
                )
        except Exception:
            logger.debug("Failed to emit analysis result", exc_info=True)

    return event


def _commit_effect(workspace_root: Path) -> CommitEffect:
    return CommitEffect(message_file=str(workspace_root / COMMIT_MESSAGE_ARTIFACT))


def _execute_effect(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    *,
    verbosity: Verbosity = Verbosity.VERBOSE,
) -> PipelineEvent:
    """Execute an effect and return the resulting event.

    Args:
        effect: Effect to execute.
        config: Unified configuration.

    Returns:
        Event resulting from effect execution.
    """
    from ralph.agents.invoke import (  # noqa: PLC0415
        AgentInvocationError,
        invoke_agent,
    )
    from ralph.agents.registry import AgentRegistry  # noqa: PLC0415
    from ralph.git.operations import create_commit, stage_all  # noqa: PLC0415

    deps = _AgentExecutionDeps(
        invoke_agent=invoke_agent,
        agent_invocation_error=AgentInvocationError,
        agent_registry=AgentRegistry,
    )

    if isinstance(effect, InvokeAgentEffect):
        return _execute_agent_effect(
            effect, config, deps, workspace_scope, display, verbosity=verbosity
        )
    if isinstance(effect, CommitEffect):
        return _execute_commit_effect(effect, create_commit, stage_all, workspace_scope.root)
    if isinstance(effect, SaveCheckpointEffect):
        return PipelineEvent.CHECKPOINT_SAVED

    logger.warning("Unknown effect type: {}", type(effect))
    return PipelineEvent.AGENT_FAILURE


def _execute_agent_effect(  # noqa: PLR0913
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    deps: _AgentExecutionDeps,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    *,
    verbosity: Verbosity = Verbosity.VERBOSE,
) -> PipelineEvent:
    _emit_display_line(display, None, _status_text("Invoking agent", effect.agent_name, "cyan"))
    registry = deps.agent_registry.from_config(config)
    agent_config = registry.get(effect.agent_name)
    if agent_config is None:
        logger.error("Agent not found: {}", effect.agent_name)
        return PipelineEvent.AGENT_FAILURE

    if display is None or isinstance(display, _LegacyConsoleDisplay):
        show_phase_start(effect.phase, agent_name=effect.agent_name, console=console)

    bridge = None
    try:
        from ralph.agents.invoke import InvokeOptions  # noqa: PLC0415

        session = AgentSession(
            session_id=f"{effect.phase}-{uuid.uuid4().hex[:8]}",
            run_id=str(uuid.uuid4()),
            drain=effect.drain or effect.phase,
            capabilities=_default_mcp_capabilities_for_phase(effect.drain or effect.phase),
        )
        workspace = FsWorkspace(
            workspace_scope.root,
            allowed_roots=workspace_scope.allowed_roots,
        )
        bridge = start_mcp_server(session, workspace)

        options = InvokeOptions(
            verbose=config.general.verbosity >= _VERBOSE_LOG_LEVEL,
            show_progress=False,
            workspace_path=workspace_scope.root,
            extra_env={
                MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri(),
                MCP_RUN_ID_ENV: session.run_id,
            },
            system_prompt_file=materialize_system_prompt(
                workspace_root=workspace_scope.root,
                name=str(effect.phase),
            ),
        )
        output_lines = deps.invoke_agent(agent_config, effect.prompt_file, options=options)
        if _verbosity_rank(verbosity) >= _VERBOSITY_RANK[Verbosity.NORMAL]:
            _stream_parsed_agent_activity(
                output_lines,
                str(agent_config.json_parser),
                effect.agent_name,
                display,
            )
        else:
            for _ in output_lines:
                pass
    except deps.agent_invocation_error as exc:
        logger.error("Agent invocation failed: {}", exc)
        return PipelineEvent.AGENT_FAILURE
    except Exception:
        logger.exception("Unexpected error during agent invocation: {}")
        return PipelineEvent.AGENT_FAILURE
    finally:
        if bridge is not None:
            shutdown_mcp_server(bridge)
    return PipelineEvent.AGENT_SUCCESS


def _default_mcp_capabilities_for_phase(phase: str) -> set[str]:
    drain_class = drain_class_for_session(phase)
    base = {
        "workspace.read",
        "git.status_read",
        "git.diff_read",
        "artifact.submit",
    }

    if drain_class in {DrainClass.PLANNING, DrainClass.ANALYSIS, DrainClass.REVIEW}:
        return base
    if drain_class is DrainClass.COMMIT:
        return base | {"workspace.write_ephemeral", "git.write", "run.report_progress"}
    if drain_class in {DrainClass.DEVELOPMENT, DrainClass.FIX}:
        return base | {
            "workspace.write_ephemeral",
            "workspace.write_tracked",
            "process.exec_bounded",
            "run.report_progress",
            "env.read",
        }
    return base


def _execute_commit_effect(
    effect: CommitEffect,
    create_commit: Callable[[str, str], str],
    stage_all: Callable[[str], None],
    repo_root: Path,
) -> PipelineEvent:
    try:
        message = _read_commit_effect_message(effect)
        if not message:
            logger.error("Commit message file is empty: {}", effect.message_file)
            return PipelineEvent.COMMIT_FAILURE
        if not _repo_has_commit_work(repo_root):
            logger.info("Skipping commit because the worktree is empty")
            _cleanup_commit_message_artifacts(repo_root)
            return PipelineEvent.COMMIT_SKIPPED
        stage_all(str(repo_root))
        sha = create_commit(str(repo_root), message)
        logger.info("Created commit: {}", sha[:8])
        _cleanup_commit_message_artifacts(repo_root)
    except Exception as exc:
        logger.error("Commit failed: {}", exc)
        return PipelineEvent.COMMIT_FAILURE
    return PipelineEvent.COMMIT_SUCCESS


def _read_commit_effect_message(effect: CommitEffect) -> str:
    return read_commit_message_from_path(Path(effect.message_file)) or ""


def _repo_has_commit_work(repo_root: Path) -> bool:
    return Repo(repo_root).is_dirty(untracked_files=True)


def _cleanup_commit_message_artifacts(repo_root: Path) -> None:
    delete_commit_message_artifacts(repo_root)


def _subscriber_for_display(
    display: ParallelDisplay | _LegacyConsoleDisplay | None,
) -> DashboardSubscriber | None:
    """Extract the dashboard subscriber from a display, when one is exposed."""
    if display is None or isinstance(display, _LegacyConsoleDisplay):
        return None
    if not hasattr(display, "subscriber"):
        return None
    return cast("DashboardSubscriber | None", display.subscriber)


def _record_activity_on_subscriber(
    subscriber: DashboardSubscriber,
    parsed_line: AgentOutputLine,
    rendered: Text | None,
    agent_name: str,
) -> None:
    try:
        if rendered is None:
            line_text = ""
        else:
            line_text = rendered.plain
        tool_name: str | None = None
        if parsed_line.type == "tool_use":
            stripped = parsed_line.content.strip()
            if stripped:
                tool_name = stripped
        subscriber.record_activity(
            unit_id=agent_name,
            agent_name=agent_name,
            line=line_text,
            tool_name=tool_name,
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("subscriber.record_activity failed", exc_info=True)


def _stream_parsed_agent_activity(
    lines: Iterable[object],
    parser_type: str,
    agent_name: str,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
) -> None:
    parser = _resolve_parser(parser_type)
    str_lines = (str(line) for line in lines)
    subscriber = _subscriber_for_display(display)
    for parsed_line in parser.parse(str_lines):
        rendered = _render_agent_activity_line(parsed_line, agent_name)
        if rendered is not None:
            _emit_display_line(display, None, rendered)
        if subscriber is not None:
            _record_activity_on_subscriber(subscriber, parsed_line, rendered, agent_name)


def _resolve_parser(parser_type: str) -> AgentParser:
    try:
        return get_parser(parser_type)
    except ValueError:
        logger.warning("Unknown parser '{}'; falling back to generic", parser_type)
        return get_parser("generic")


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length, appending ellipsis if truncated."""
    if max_length <= 1 or len(text) <= max_length:
        return text
    return text[:max_length] + "…"


def _render_agent_activity_line(output: AgentOutputLine, agent_name: str) -> Text | None:
    rendered: Text | None = None

    if output.type == "text":
        content = output.content.strip()
        if content:
            rendered = _styled_prefix(agent_name, "white")
            text_width = min(_MAX_TEXT_LENGTH, _available_width(len(agent_name) + 2))
            rendered.append(_truncate(content, text_width))
    elif output.type == "tool_use":
        tool_name = output.content.strip() or "unknown-tool"
        prefix_label = f"{agent_name} tool"
        rendered = _styled_prefix(prefix_label, "magenta")
        rendered.append(tool_name, style="bold magenta")
        input_summary = _tool_input_summary(output.metadata)
        if input_summary:
            prefix_total = len(prefix_label) + 2 + len(tool_name) + 3
            tool_input_width = min(_MAX_TOOL_INPUT_LENGTH, _available_width(prefix_total))
            truncated = _truncate(input_summary, tool_input_width)
            rendered.append(f" ({truncated})", style="dim")
    elif output.type == "tool_result":
        result = output.content.strip()
        if result:
            result_label = f"{agent_name} result"
            rendered = _styled_prefix(result_label, "dim")
            result_prefix_len = len(result_label) + 2
            if len(result) > _TOOL_RESULT_BRIEF_THRESHOLD:
                brief_width = min(_MAX_TOOL_RESULT_BRIEF, _available_width(result_prefix_len))
                rendered.append(_truncate(result, brief_width), style="dim")
            else:
                result_width = min(_MAX_TOOL_RESULT_LENGTH, _available_width(result_prefix_len))
                rendered.append(_truncate(result, result_width), style="dim")
    elif output.type == "error":
        error = output.content.strip() or "unknown error"
        rendered = _styled_prefix(f"{agent_name} ✗", "red")
        rendered.append(error, style="red")
    else:
        summary = _event_summary(output)
        rendered = _styled_prefix(f"{agent_name} {output.type}", "dim")
        rendered.append(summary)

    return rendered


def _styled_prefix(label: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    return text


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


def _prompt_session_drain_for_phase(phase: str) -> SessionDrain:
    drain_map = {
        "planning": SessionDrain.PLANNING,
        "development": SessionDrain.DEVELOPMENT,
        "development_analysis": SessionDrain.DEVELOPMENT_ANALYSIS,
        "development_commit": SessionDrain.DEVELOPMENT_COMMIT,
        "review": SessionDrain.REVIEW,
        "review_analysis": SessionDrain.REVIEW_ANALYSIS,
        "review_commit": SessionDrain.REVIEW_COMMIT,
        "fix": SessionDrain.FIX,
    }
    return drain_map.get(phase, SessionDrain.COMMIT)


def _event_summary(output: AgentOutputLine) -> str:
    content = output.content.strip()
    if content:
        return content

    if output.metadata:
        summary = _metadata_summary(output.metadata)
        if summary:
            return summary

    return "(no details)"


def _tool_input_summary(metadata: dict[str, object]) -> str:
    input_obj = metadata.get("input")
    if isinstance(input_obj, dict):
        return _metadata_summary(cast("dict[str, object]", input_obj))
    return ""


def _metadata_summary(metadata: dict[str, object]) -> str:
    preferred_keys = (
        "status",
        "summary",
        "phase",
        "tool",
        "name",
        "command",
        "workdir",
        "path",
        "result",
        "output",
        "error",
        "message",
    )

    parts: list[str] = []
    for key in preferred_keys:
        if key not in metadata:
            continue
        value = _format_metadata_value(metadata[key])
        if value:
            parts.append(f"{key}={value}")

    if parts:
        result = "; ".join(parts)
        return _truncate(result, _MAX_METADATA_SUMMARY_LENGTH)

    for key, value_obj in metadata.items():
        value = _format_metadata_value(value_obj)
        if value:
            parts.append(f"{key}={value}")
        if len(parts) >= _MAX_METADATA_PARTS:
            break

    result = "; ".join(parts)
    return _truncate(result, _MAX_METADATA_SUMMARY_LENGTH)


def _format_metadata_value(value: object) -> str:
    formatted = ""
    if isinstance(value, str):
        formatted = value.strip()
    elif isinstance(value, (bool, int, float)):
        formatted = str(value)
    elif isinstance(value, dict):
        dict_value = cast("dict[str, object]", value)
        nested = _metadata_summary(dict_value)
        formatted = nested or f"{len(dict_value)} field(s)"
    elif isinstance(value, list):
        if not value:
            return formatted
        formatted = _format_list_metadata_value(value)
    return formatted


def _format_list_metadata_value(value: list[object]) -> str:
    scalar_items: list[str] = []
    for item in value:
        if isinstance(item, (str, int, float, bool)):
            item_str = str(item).strip()
            if item_str:
                scalar_items.append(item_str)
        else:
            return f"{len(value)} item(s)"
    return ", ".join(scalar_items)
