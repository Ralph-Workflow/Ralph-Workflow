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
from collections.abc import Callable
from contextlib import suppress
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
from ralph.display.artifact_renderer import (
    render_analysis_decision,
    render_commit_message,
    render_development_artifact,
    render_fix_artifact,
    render_plan_artifact,
    render_review_artifact,
)
from ralph.display.phase_banner import (
    PhaseStartContext,
    show_phase_complete,
    show_phase_start,
    show_phase_transition,
)
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    read_commit_message_from_path,
)
from ralph.mcp.protocol.capability_mapping import DrainClass, drain_class_for_session
from ralph.mcp.protocol.session import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV, AgentSession
from ralph.mcp.server.lifecycle import shutdown_mcp_server, start_mcp_server
from ralph.phases import PhaseContext, handle_phase
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    ExitFailureEffect,
    ExitSuccessEffect,
    FanOutDevelopmentEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RebaseState
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.loader import load_policy_or_die
from ralph.process.manager import process_phase_scope
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
    from collections.abc import Callable, Iterable, Iterator

    from ralph.agents.executor import AgentExecutor
    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.git.executor import GitExecutor
    from ralph.interrupt.asyncio_bridge import SignalBridge
    from ralph.mcp.upstream.agent_probe import AgentProbeReport
    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.mcp.upstream.validation import UpstreamValidationReport
    from ralph.pipeline.parallel import coordinator as parallel_coordinator
    from ralph.pipeline.work_units import WorkUnit
    from ralph.policy.models import AgentsPolicy, PhaseDefinition, PipelinePolicy, PolicyBundle

    class _PipelineSubscriber(Protocol):
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
_DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS = 120.0
_AGENT_IDLE_TIMEOUT_ENV = "RALPH_AGENT_IDLE_TIMEOUT_SECONDS"
_RECOVERY_CONTEXT_LINES = 12
_TRANSIENT_CONNECTIVITY_MARKERS = (
    "connection refused",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "timed out",
    "timeout",
    "offline",
    "econnreset",
    "enotfound",
    "socket hang up",
)
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


@dataclass(frozen=True)
class _AgentRecoveryPlan:
    prompt_file: str
    session_id: str | None
    reason: str


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


def _validate_custom_mcp_servers(workspace_root: Path) -> int:
    """Fail-fast validation of custom MCP servers + per-agent transports.

    Returns the exit code the runner should propagate (0 to continue, 1 to abort).
    Tests can monkeypatch ``_VALIDATE_MCP`` and ``_PROBE_AGENT_TRANSPORTS`` to
    drive deterministic outcomes without spawning real upstream servers.
    """
    from ralph.mcp.transport.common import mcp_toml_as_upstreams  # noqa: PLC0415
    from ralph.mcp.upstream.validation import (  # noqa: PLC0415
        UpstreamValidationError,
        strict_mode_from_env,
    )

    upstreams = mcp_toml_as_upstreams(workspace_root)
    if not upstreams:
        return 0

    strict = strict_mode_from_env()
    try:
        upstream_report = _VALIDATE_MCP(upstreams, strict=strict)
    except UpstreamValidationError as exc:
        logger.error("Custom MCP servers failed startup validation:\n{}", exc)
        return 1

    healthy_names = {r.name for r in upstream_report.servers if r.ok}
    healthy_servers = tuple(s for s in upstreams if s.name in healthy_names)
    if not healthy_servers:
        return 0

    probe_results = _PROBE_AGENT_TRANSPORTS(healthy_servers, workspace_path=workspace_root)
    failures = [p for p in probe_results if not p.ok]
    if failures and strict:
        for failure in failures:
            logger.error(
                "Agent transport probe failed: server={} transport={} error={}",
                failure.server_name,
                failure.transport,
                failure.error,
            )
        return 1
    for failure in failures:
        logger.warning(
            "Agent transport probe failed (soft mode): server={} transport={} error={}",
            failure.server_name,
            failure.transport,
            failure.error,
        )
    return 0


def _default_validate_mcp(
    servers: Iterable[UpstreamMcpServer], *, strict: bool
) -> UpstreamValidationReport:
    from ralph.mcp.upstream.validation import (  # noqa: PLC0415
        validate_upstream_mcp_servers,
    )

    return validate_upstream_mcp_servers(servers, strict=strict)


def _default_probe_agent_transports(
    servers: Iterable[UpstreamMcpServer], *, workspace_path: Path | None
) -> tuple[AgentProbeReport, ...]:
    from ralph.mcp.upstream.agent_probe import (  # noqa: PLC0415
        probe_agent_transports,
    )

    return probe_agent_transports(servers, workspace_path=workspace_path)


_VALIDATE_MCP = _default_validate_mcp
_PROBE_AGENT_TRANSPORTS = _default_probe_agent_transports


class _LegacyConsoleDisplay:
    @property
    def console(self) -> Console:
        return console

    def __enter__(self) -> _LegacyConsoleDisplay:
        return self

    def __exit__(self, _exc_type: object, _exc_val: object, _exc_tb: object) -> None:
        return None

    def emit(self, unit_id: str | None, line: Text | str) -> None:
        if unit_id is None:
            console.print(line)
            return
        console.print(f"[{unit_id}] {line}")


def _display_console(
    display: ParallelDisplay | _LegacyConsoleDisplay | None,
) -> Console:
    if display is None:
        return console
    return display.console


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
    transitive Rich/panel dependencies) cannot be imported or initialized.
    The display owns its own PipelineSubscriber so the runner and the live
    render thread share a single subscriber.
    """
    try:
        from ralph.display.parallel_display import (  # noqa: PLC0415
            ParallelDisplay as _ParallelDisplay,
        )

        return _ParallelDisplay(
            console=console,
            env=dict(os.environ),
            workspace_root=workspace_root,
            run_id=str(uuid.uuid4()),
        )
    except Exception:
        logger.debug(
            "ParallelDisplay unavailable or failed to initialize; falling back to legacy console",
            exc_info=True,
        )
        return _LegacyConsoleDisplay()


def _execute_effect_with_optional_display(  # noqa: PLR0913
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
) -> Event:
    params = signature(_execute_effect).parameters
    has_display = "display" in params
    has_verbosity = "verbosity" in params
    has_state = "state" in params

    result: Event
    if len(params) == _LEGACY_EXECUTE_EFFECT_ARITY:
        result = _execute_effect(effect, config, workspace_scope)
    elif has_display:
        if has_verbosity and has_state:
            result = _execute_effect(
                effect,
                config,
                workspace_scope,
                display=display,
                verbosity=verbosity,
                state=state,
            )
        elif has_verbosity:
            result = _execute_effect(
                effect,
                config,
                workspace_scope,
                display=display,
                verbosity=verbosity,
            )
        elif has_state:
            result = _execute_effect(
                effect,
                config,
                workspace_scope,
                display=display,
                state=state,
            )
        else:
            result = _execute_effect(effect, config, workspace_scope, display=display)
    elif has_verbosity and has_state:
        result = _execute_effect(
            effect,
            config,
            workspace_scope,
            verbosity=verbosity,
            state=state,
        )
    elif has_verbosity:
        result = _execute_effect(effect, config, workspace_scope, verbosity=verbosity)
    elif has_state:
        result = _execute_effect(effect, config, workspace_scope, state=state)
    else:
        result = _execute_effect(effect, config, workspace_scope)
    return result


def _invoke_execute_effect_with_optional_display(  # noqa: PLR0913
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | _LegacyConsoleDisplay | None,
    verbosity: Verbosity,
    state: PipelineState,
) -> Event:
    params = signature(_execute_effect_with_optional_display).parameters
    has_state = "state" in params
    has_verbosity = "verbosity" in params

    if has_state and has_verbosity:
        return _execute_effect_with_optional_display(
            effect,
            config,
            workspace_scope,
            display=display,
            verbosity=verbosity,
            state=state,
        )
    if has_verbosity:
        return _execute_effect_with_optional_display(
            effect,
            config,
            workspace_scope,
            display=display,
            verbosity=verbosity,
        )
    return _execute_effect_with_optional_display(
        effect,
        config,
        workspace_scope,
        display=display,
    )


def _notify_dashboard_subscriber(
    dashboard_subscriber: _PipelineSubscriber | None,
    state: PipelineState,
) -> None:
    if dashboard_subscriber is None:
        return
    dashboard_subscriber.notify(state)


def _notify_pipeline_subscriber(
    pipeline_subscriber: _PipelineSubscriber | None,
    state: PipelineState,
) -> None:
    _notify_dashboard_subscriber(pipeline_subscriber, state)



def _reduce_runtime_recovery(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    *,
    reason: str,
) -> PipelineState:
    failure_event = PhaseFailureEvent(
        phase=state.phase,
        reason=reason,
        recoverable=True,
    )
    recovered_state, _ = reducer_reduce(state, failure_event, pipeline_policy)
    return recovered_state


def _save_checkpoint_or_log(
    state: PipelineState,
    *,
    message: str,
) -> None:
    try:
        ckpt.save(state)
    except Exception as exc:
        logger.exception(message, phase=state.phase, err=exc)


def _run_pipeline_step(  # noqa: PLR0913
    *,
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
    display: ParallelDisplay | _LegacyConsoleDisplay,
    verbosity: Verbosity,
    registry: _RegistryLike,
    pipeline_subscriber: _PipelineSubscriber | None,
) -> PipelineState | int:
    try:
        effect = _call_determine_effect_from_policy(state, policy_bundle, workspace_scope, config)
        inline_result = _handle_inline_effect(
            effect=effect,
            state=state,
            pipeline_policy=policy_bundle.pipeline,
            workspace_scope=workspace_scope,
            display=display,
            pipeline_subscriber=pipeline_subscriber,
        )
        if inline_result is not None:
            return inline_result

        if isinstance(effect, FanOutDevelopmentEffect):
            return _execute_fan_out_sync(
                effect=effect,
                state=state,
                display=display,
                policy_bundle=policy_bundle,
                workspace_scope=workspace_scope,
                pipeline_subscriber=pipeline_subscriber,
            )

        with process_phase_scope(state.phase):
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
            event = _invoke_execute_effect_with_optional_display(
                effect,
                config,
                workspace_scope,
                display=display,
                verbosity=verbosity,
                state=state,
            )
            if isinstance(effect, InvokeAgentEffect) and event == PipelineEvent.AGENT_SUCCESS:
                event = _phase_event_after_agent_run(
                    effect=effect,
                    config=config,
                    policy_bundle=policy_bundle,
                    workspace=workspace,
                    workspace_scope=workspace_scope,
                    display=display,
                )

        next_state, _ = reducer_reduce(state, event, policy_bundle.pipeline)
        _notify_pipeline_subscriber(pipeline_subscriber, next_state)
        _save_checkpoint_or_log(
            next_state,
            message=(
                "Checkpoint save failed in phase={phase}: {err} "
                "-- continuing without checkpoint"
            ),
        )
        return next_state
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        logger.exception(
            "Pipeline step crashed in phase={phase}: {err}",
            phase=state.phase,
            err=exc,
        )
        recovered_state = _reduce_runtime_recovery(
            state,
            policy_bundle.pipeline,
            reason=f"Pipeline step crashed: {type(exc).__name__}: {exc}",
        )
        _notify_pipeline_subscriber(pipeline_subscriber, recovered_state)
        _save_checkpoint_or_log(
            recovered_state,
            message="Checkpoint save failed while recording recovery in phase={phase}: {err}",
        )
        return recovered_state


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


def _show_phase_start_with_context(
    phase: str,
    agent_name: str,
    console: Console | None,
    state: PipelineState | None,
) -> None:
    """Helper to call show_phase_start with PhaseStartContext when state is available."""
    if state is None:
        show_phase_start(phase, agent_name=agent_name, console=console)
        return

    # Build PhaseStartContext from state
    ctx = PhaseStartContext(
        iteration=state.iteration,
        total_iterations=state.total_iterations,
        reviewer_pass=state.reviewer_pass,
        total_reviewer_passes=state.total_reviewer_passes,
        development_analysis_iteration=state.development_analysis_iteration,
        max_development_analysis_iterations=state.max_development_analysis_iterations,
        review_analysis_iteration=state.review_analysis_iteration,
        max_review_analysis_iterations=state.max_review_analysis_iterations,
    )
    show_phase_start(phase, ctx=ctx, agent_name=agent_name, console=console)


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

    # Emit phase completion for the phase we're leaving
    try:
        show_phase_complete(previous_phase)
    except Exception:  # pragma: no cover - defensive
        logger.debug("show_phase_complete failed", exc_info=True)

    # Emit transition to the new phase
    context = _phase_context(state, previous_phase) or None
    try:
        show_phase_transition(previous_phase, state.phase, context=context)
    except Exception:  # pragma: no cover - defensive
        logger.debug("show_phase_transition failed", exc_info=True)
    return state.phase


def _emit_final_summary(
    state: PipelineState,
    workspace_root: Path,
    *,
    subscriber: PipelineSubscriber | None = None,
) -> None:
    """Emit an end-of-run completion summary panel.

    Called unconditionally after the pipeline loop exits (including via
    exception) so the user sees a final summary of what Ralph did, what
    was decided, and whether verification passed.

    When a ``subscriber`` is supplied, the snapshot is built from its
    accumulated state (decision log, analysis, plan) so the panel mirrors
    what the live display showed during the run.
    """
    try:
        from ralph.display.completion_summary import emit_completion_summary  # noqa: PLC0415
        from ralph.display.snapshot import snapshot_from_state  # noqa: PLC0415

        dropped_count = 0
        snapshot = None
        if subscriber is not None:
            try:
                dropped_count = subscriber.dropped_count
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
        emit_completion_summary(
            console,
            snapshot,
            workspace_root=workspace_root,
            dropped_count=dropped_count,
        )
    except Exception:
        logger.debug("Failed to emit completion summary", exc_info=True)


def run(  # noqa: PLR0913
    config: UnifiedConfig,
    initial_state: PipelineState | None = None,
    display: ParallelDisplay | None = None,
    pipeline_subscriber: _PipelineSubscriber | None = None,
    *,
    dashboard_subscriber: _PipelineSubscriber | None = None,
    verbosity: Verbosity | None = None,
) -> int:
    """Execute the pipeline event loop.

    Args:
        config: Unified configuration for the pipeline.
        initial_state: Optional initial state (for resume from checkpoint).
        display: Optional pre-built display. When omitted, a ParallelDisplay
            is constructed by default unless ``verbosity`` is QUIET.
        pipeline_subscriber: Optional subscriber that will receive notify(state)
            calls after each reduce. When a ParallelDisplay is constructed by
            this function, its built-in subscriber is wired in automatically.
        verbosity: Optional explicit verbosity. Defaults to the configured
            value in ``config.general.verbosity`` (mapped from int rank).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    workspace_scope = resolve_workspace_scope()
    _write_start_commit_if_absent(workspace_scope.root)
    if _validate_custom_mcp_servers(workspace_scope.root) != 0:
        return 1
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

    if pipeline_subscriber is None:
        pipeline_subscriber = dashboard_subscriber

    if display is not None:
        active_display: ParallelDisplay | _LegacyConsoleDisplay = display
    elif is_quiet:
        active_display = _LegacyConsoleDisplay()
    else:
        active_display = _build_default_display(workspace_scope.root)

    effective_pipeline_subscriber = dashboard_subscriber or pipeline_subscriber
    if effective_pipeline_subscriber is None and hasattr(active_display, "subscriber"):
        effective_pipeline_subscriber = cast(
            "_PipelineSubscriber | None",
            getattr(active_display, "subscriber", None),
        )

    exit_code = 0
    _prev_phase = state.phase
    try:
        with active_display:
            _notify_pipeline_subscriber(effective_pipeline_subscriber, state)
            try:
                while state.phase != PHASE_COMPLETE:
                    step_result = _run_pipeline_step(
                        state=state,
                        policy_bundle=policy_bundle,
                        workspace_scope=workspace_scope,
                        config=config,
                        display=active_display,
                        verbosity=effective_verbosity,
                        registry=registry,
                        pipeline_subscriber=effective_pipeline_subscriber,
                    )
                    if isinstance(step_result, int):
                        return step_result
                    state = step_result
                    _prev_phase = _emit_phase_transition_if_changed(
                        active_display,
                        _prev_phase,
                        state,
                        verbosity=effective_verbosity,
                    )

            except KeyboardInterrupt:
                logger.warning("Interrupted by user; saving checkpoint.")
                interrupted_state = state.copy_with(interrupted_by_user=True)
                _save_checkpoint_or_log(
                    interrupted_state,
                    message=(
                        "Checkpoint save failed while handling interrupt in phase={phase}: {err}"
                    ),
                )
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
            subscriber=cast("PipelineSubscriber | None", effective_pipeline_subscriber),
        )
    return exit_code


def _fan_out_display_and_subscriber(
    display: ParallelDisplay | _LegacyConsoleDisplay,
    pipeline_subscriber: _PipelineSubscriber | None,
    dashboard_subscriber: _PipelineSubscriber | None,
) -> tuple[ParallelDisplay, _PipelineSubscriber | None]:
    from ralph.display.parallel_display import ParallelDisplay as _ParallelDisplay  # noqa: PLC0415

    parallel_display: ParallelDisplay = (
        display if isinstance(display, _ParallelDisplay) else _ParallelDisplay(console)
    )
    effective_pipeline_subscriber = dashboard_subscriber or pipeline_subscriber
    if effective_pipeline_subscriber is None and hasattr(parallel_display, "subscriber"):
        effective_pipeline_subscriber = cast(
            "_PipelineSubscriber | None",
            getattr(parallel_display, "subscriber", None),
        )
    return parallel_display, effective_pipeline_subscriber


def _fan_out_worker_context(
    *,
    workspace_scope: WorkspaceScope,
    repo_root: Path,
    git_exec: GitExecutor,
    bridge: SignalBridge,
) -> tuple[AgentExecutor, parallel_coordinator._WorkerContext]:
    from ralph.agents.subprocess_executor import SubprocessAgentExecutor  # noqa: PLC0415
    from ralph.git.worktree_manager import WorktreeManager  # noqa: PLC0415
    from ralph.mcp.server.factory_impl import DynamicBindingMcpServerFactory  # noqa: PLC0415
    from ralph.pipeline.parallel import coordinator  # noqa: PLC0415

    executor = cast(
        "AgentExecutor",
        SubprocessAgentExecutor(_parallel_worker_command(), signal_bridge=bridge),
    )
    workspace = FsWorkspace(
        workspace_scope.root,
        allowed_roots=workspace_scope.allowed_roots,
    )
    return executor, coordinator._WorkerContext(
        log=coordinator._WorkerLog(
            log_dir=workspace_scope.root / ".agent" / "logs",
            run_id=str(uuid.uuid4()),
        ),
        isolation=coordinator._IsolationDeps(
            worktree_manager=WorktreeManager(repo_root),
            mcp_factory=DynamicBindingMcpServerFactory(workspace=workspace),
            repo_root=repo_root,
            executor_command=_parallel_worker_command(),
            signal_bridge=bridge,
        ),
    )


def _resume_fan_out_state(
    state: PipelineState,
    effect: FanOutDevelopmentEffect,
    pipeline_policy: PipelinePolicy,
    pipeline_subscriber: _PipelineSubscriber | None,
) -> tuple[PipelineState, tuple[WorkUnit, ...]]:
    resumed_state, _ = reducer_reduce(state, PipelineEvent.WORKERS_RESUMED, pipeline_policy)
    _notify_pipeline_subscriber(pipeline_subscriber, resumed_state)
    completed_ids = {
        uid
        for uid, ws in resumed_state.worker_states.items()
        if ws.status == WorkerStatus.SUCCEEDED
    }
    resume_units = tuple(u for u in effect.work_units if u.unit_id not in completed_ids)
    return resumed_state, resume_units


async def _run_fan_out_async(  # noqa: PLR0913
    *,
    effect: FanOutDevelopmentEffect,
    state: PipelineState,
    display: ParallelDisplay,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    repo_root: Path,
    git_exec: GitExecutor,
    pipeline_subscriber: _PipelineSubscriber | None,
) -> PipelineState:
    import asyncio  # noqa: PLC0415

    from ralph.interrupt.asyncio_bridge import (  # noqa: PLC0415
        SignalBridge,
        install_signal_handlers,
    )
    from ralph.pipeline.parallel import coordinator, merge_integrator  # noqa: PLC0415

    current = state
    try:
        loop = asyncio.get_running_loop()
        bridge = SignalBridge()
        root_task = cast("asyncio.Task[object] | None", asyncio.current_task())
        assert root_task is not None
        install_signal_handlers(loop, root_task, bridge)
        executor, worker_ctx = _fan_out_worker_context(
            workspace_scope=workspace_scope,
            repo_root=repo_root,
            git_exec=git_exec,
            bridge=bridge,
        )
        current, resume_units = _resume_fan_out_state(
            state,
            effect,
            policy_bundle.pipeline,
            pipeline_subscriber,
        )
        if not resume_units:
            return current

        fan_out_events = await coordinator.run_fan_out(
            effect=FanOutDevelopmentEffect(work_units=resume_units, max_workers=effect.max_workers),
            executor=executor,
            display=display,
            ctx=worker_ctx,
        )
        for ev in fan_out_events:
            current, _ = reducer_reduce(current, ev, policy_bundle.pipeline)
            _notify_pipeline_subscriber(pipeline_subscriber, current)
        _save_checkpoint_or_log(
            current,
            message="Checkpoint save failed after fan-out in phase={phase}: {err}",
        )

        merge_result = await merge_integrator.integrate(
            base_branch="main",
            worker_states=current.worker_states,
            git_executor=git_exec,
            repo_root=repo_root,
        )
        for ev in merge_result.events:
            current, _ = reducer_reduce(current, ev, policy_bundle.pipeline)
            _notify_pipeline_subscriber(pipeline_subscriber, current)
        _save_checkpoint_or_log(
            current,
            message="Checkpoint save failed after merge integration in phase={phase}: {err}",
        )
        return current
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        logger.exception(
            "Fan-out execution crashed in phase={phase}: {err}",
            phase=current.phase,
            err=exc,
        )
        recovered = _reduce_runtime_recovery(
            current,
            policy_bundle.pipeline,
            reason=f"Fan-out execution crashed: {type(exc).__name__}: {exc}",
        )
        _notify_pipeline_subscriber(pipeline_subscriber, recovered)
        _save_checkpoint_or_log(
            recovered,
            message=(
                "Checkpoint save failed while recording fan-out recovery in phase={phase}: "
                "{err}"
            ),
        )
        return recovered


def _execute_fan_out_sync(  # noqa: PLR0913
    *,
    effect: FanOutDevelopmentEffect,
    state: PipelineState,
    display: ParallelDisplay | _LegacyConsoleDisplay,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    pipeline_subscriber: _PipelineSubscriber | None = None,
    dashboard_subscriber: _PipelineSubscriber | None = None,
) -> PipelineState:
    """Execute fan-out development synchronously by wrapping asyncio.run()."""
    import asyncio  # noqa: PLC0415

    from ralph.git.executor import GitExecutor  # noqa: PLC0415

    parallel_display, effective_pipeline_subscriber = _fan_out_display_and_subscriber(
        display,
        pipeline_subscriber,
        dashboard_subscriber,
    )
    return asyncio.run(
        _run_fan_out_async(
            effect=effect,
            state=state,
            display=parallel_display,
            policy_bundle=policy_bundle,
            workspace_scope=workspace_scope,
            repo_root=workspace_scope.root,
            git_exec=GitExecutor(),
            pipeline_subscriber=effective_pipeline_subscriber,
        )
    )


def _parallel_worker_command() -> tuple[str, ...]:
    return (sys.executable, "-m", "ralph")


def _handle_inline_effect(  # noqa: PLR0913
    *,
    effect: Effect,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    pipeline_subscriber: _PipelineSubscriber | None = None,
    dashboard_subscriber: _PipelineSubscriber | None = None,
) -> PipelineState | int | None:
    effective_pipeline_subscriber = dashboard_subscriber or pipeline_subscriber

    if isinstance(effect, SaveCheckpointEffect):
        ckpt.save(state)
        new_state, _ = reducer_reduce(state, PipelineEvent.CHECKPOINT_SAVED, pipeline_policy)
        _notify_pipeline_subscriber(effective_pipeline_subscriber, new_state)
        return new_state

    if isinstance(effect, PreparePromptEffect):
        _materialize_prepared_prompt(effect, pipeline_policy, workspace_scope)
        updated_state = state.copy_with(
            phase=effect.phase,
            iteration=effect.iteration,
            current_drain=effect.drain or resolve_phase_drain(effect.phase, pipeline_policy),
        )
        ckpt.save(updated_state)
        _notify_pipeline_subscriber(effective_pipeline_subscriber, updated_state)
        return updated_state

    if isinstance(effect, ExitSuccessEffect):
        _emit_display_line(display, None, "[green]Pipeline completed successfully.[/green]")
        return 0

    if isinstance(effect, ExitFailureEffect):
        _emit_display_line(
            display,
            None,
            _status_text("Recovery triggered", effect.reason, "yellow"),
        )
        current_epoch = state.recovery_epoch if isinstance(state.recovery_epoch, int) else 0
        recovered_state = state.copy_with(
            phase=PHASE_FAILED,
            previous_phase=state.phase,
            last_error=effect.reason,
            recovery_epoch=current_epoch + 1,
        )
        ckpt.save(recovered_state)
        _notify_pipeline_subscriber(effective_pipeline_subscriber, recovered_state)
        return recovered_state

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
        max_development_analysis_iterations=config.general.max_development_analysis_iterations,
        max_review_analysis_iterations=config.general.max_review_analysis_iterations,
        development_analysis_iteration=0,
        review_analysis_iteration=0,
    )


def _call_determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
) -> Effect:
    determine_effect = _determine_effect_from_policy
    params = signature(determine_effect).parameters
    if "config" in params:
        return determine_effect(state, policy_bundle, workspace_scope, config=config)

    positional = [
        param
        for param in params.values()
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    if (
        any(param.kind == param.VAR_POSITIONAL for param in params.values())
        or len(positional) >= _LEGACY_EXECUTE_EFFECT_ARITY
    ):
        return determine_effect(state, policy_bundle, workspace_scope)
    return determine_effect(state, policy_bundle)


def _recovery_prepare_effect(state: PipelineState) -> PreparePromptEffect:
    previous_phase = state.previous_phase if isinstance(state.previous_phase, str) else None
    policy_entry_phase = (
        state.policy_entry_phase if isinstance(state.policy_entry_phase, str) else PHASE_PLANNING
    )
    target_phase = previous_phase or policy_entry_phase
    if target_phase == PHASE_FAILED:
        target_phase = policy_entry_phase
    drain = state.current_drain if isinstance(state.current_drain, str) else None
    return PreparePromptEffect(
        phase=target_phase,
        iteration=state.iteration,
        drain=drain,
    )


def _terminal_phase_effect(state: PipelineState) -> Effect | None:
    if state.phase == PHASE_COMPLETE:
        return ExitSuccessEffect()
    if state.phase == PHASE_FAILED:
        return _recovery_prepare_effect(state)
    return None


def _determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope | None = None,
    *,
    config: UnifiedConfig | None = None,
) -> Effect:
    terminal = _terminal_phase_effect(state)
    if terminal is not None:
        return terminal

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return ExitFailureEffect(reason=f"Unknown phase: {state.phase}")

    if phase_def.requires_commit:
        scope = workspace_scope or resolve_workspace_scope()
        return _commit_phase_effect(state, policy_bundle, phase_def, scope, config=config)

    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle, config=config)
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
    *,
    config: UnifiedConfig | None = None,
) -> Effect:
    if state.commit.agent_invoked:
        return _commit_effect(workspace_scope.root)
    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle, config=config)
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
    policy_drain: str | None = None
    if pipeline_policy is not None:
        phase_def = pipeline_policy.phases.get(phase)
        if phase_def is not None:
            policy_drain = phase_def.drain

    config_agents = _config_agents_for_phase(config, phase=phase, policy_drain=policy_drain)
    if config_agents:
        return config_agents

    return []


def _config_drain_candidates(*, phase: str, policy_drain: str | None) -> tuple[str, ...]:
    generic_aliases = {
        "development_analysis": "analysis",
        "review_analysis": "analysis",
        "development_commit": "commit",
        "review_commit": "commit",
    }
    ordered = [candidate for candidate in (policy_drain, phase) if candidate]
    for candidate in tuple(ordered):
        alias = generic_aliases.get(candidate)
        if alias is not None:
            ordered.append(alias)

    deduped: list[str] = []
    for candidate in ordered:
        if candidate not in deduped:
            deduped.append(candidate)
    return tuple(deduped)


def _config_agents_for_phase(
    config: UnifiedConfig | None,
    *,
    phase: str,
    policy_drain: str | None,
) -> list[str]:
    if config is None:
        return []

    drains = config.agent_drains if isinstance(config.agent_drains, dict) else {}
    chains = config.agent_chains if isinstance(config.agent_chains, dict) else {}
    for drain_name in _config_drain_candidates(phase=phase, policy_drain=policy_drain):
        chain_name = drains.get(drain_name)
        if isinstance(chain_name, str):
            chain_agents = chains.get(chain_name)
            if isinstance(chain_agents, list):
                return list(chain_agents)

        direct_chain_agents = chains.get(drain_name)
        if isinstance(direct_chain_agents, list):
            return list(direct_chain_agents)
    return []


def _agent_name_for_phase_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    *,
    config: UnifiedConfig | None = None,
) -> str | None:
    current_agent = state.current_agent()
    if current_agent is not None:
        return current_agent

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return None

    config_agents = _config_agents_for_phase(
        config,
        phase=state.phase,
        policy_drain=phase_def.drain,
    )
    if config_agents:
        return config_agents[0]

    return None


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
        console=_display_console(display),
    )
    try:
        events = handle_phase(effect, ctx)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        logger.exception(
            "Phase handler crashed in phase={phase}: {err}",
            phase=effect.phase,
            err=exc,
        )
        events = [
            PhaseFailureEvent(
                phase=effect.phase,
                reason=f"Phase handler crashed: {type(exc).__name__}: {exc}",
                recoverable=True,
            )
        ]
    event: Event = events[0] if events else PipelineEvent.AGENT_SUCCESS

    with suppress(Exception):
        _render_phase_artifact_handoff(
            effect.phase,
            event,
            Path(workspace.absolute_path(".")),
            display,
        )

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
                cast("ParallelDisplay", display).emit_analysis_result(
                    phase=effect.phase,
                    decision=summary.decision,
                    reason=summary.reason,
                )
        except Exception:
            logger.debug("Failed to emit analysis result", exc_info=True)

    return event


def _render_phase_artifact_handoff(
    phase: str,
    event: Event,
    workspace_root: Path,
    display: ParallelDisplay | _LegacyConsoleDisplay | None,
) -> None:
    console_obj = _display_console(display)

    if phase == "planning" and event == PipelineEvent.AGENT_SUCCESS:
        render_plan_artifact(workspace_root, console_obj)
        return
    if phase == "development" and event == PipelineEvent.AGENT_SUCCESS:
        render_development_artifact(workspace_root, console_obj)
        return
    if phase == "review" and event == PipelineEvent.AGENT_SUCCESS:
        render_review_artifact(workspace_root, console_obj)
        return
    if phase == "fix" and event == PipelineEvent.AGENT_SUCCESS:
        render_fix_artifact(workspace_root, console_obj)
        return
    if phase in {"development_analysis", "review_analysis"}:
        render_analysis_decision(workspace_root, phase, console_obj)



def _commit_effect(workspace_root: Path) -> CommitEffect:
    return CommitEffect(message_file=str(workspace_root / COMMIT_MESSAGE_ARTIFACT))


def _execute_effect(  # noqa: PLR0913
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
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
            effect, config, deps, workspace_scope,
            display=display, verbosity=verbosity, state=state,
        )
    if isinstance(effect, CommitEffect):
        return _execute_commit_effect(
            effect, create_commit, stage_all, workspace_scope.root, display
        )
    if isinstance(effect, SaveCheckpointEffect):
        return PipelineEvent.CHECKPOINT_SAVED

    logger.warning("Unknown effect type: {}", type(effect))
    return PipelineEvent.AGENT_FAILURE


def _execute_agent_effect(  # noqa: PLR0913
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    deps: _AgentExecutionDeps,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
) -> PipelineEvent:
    _emit_display_line(display, None, _status_text("Invoking agent", effect.agent_name, "cyan"))
    registry = deps.agent_registry.from_config(config)
    agent_config = registry.get(effect.agent_name)
    if agent_config is None:
        logger.error("Agent not found: {}", effect.agent_name)
        return PipelineEvent.AGENT_FAILURE

    _show_phase_start_with_context(
        effect.phase, effect.agent_name, _display_console(display), state
    )

    from ralph.agents.invoke import (  # noqa: PLC0415
        AgentInactivityTimeoutError,
        InvokeOptions,
        extract_session_id,
    )

    attempt_prompt_file = effect.prompt_file
    resume_session_id: str | None = None
    max_recovery_attempts = _same_agent_recovery_attempts(config)

    for attempt_index in range(max_recovery_attempts + 1):
        bridge = None
        raw_output: list[str] = []
        rendered_output: list[str] = []
        try:
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
            _clear_phase_output_artifacts(workspace, effect.phase)
            bridge = start_mcp_server(session, workspace, phase=effect.phase)

            options = InvokeOptions(
                verbose=config.general.verbosity >= _VERBOSE_LOG_LEVEL,
                show_progress=False,
                workspace_path=workspace_scope.root,
                extra_env={
                    MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri(),
                    MCP_RUN_ID_ENV: session.run_id,
                },
                idle_timeout_seconds=_agent_idle_timeout_seconds(),
                session_id=resume_session_id,
                system_prompt_file=materialize_system_prompt(
                    workspace_root=workspace_scope.root,
                    name=str(effect.phase),
                ),
            )
            output_lines = deps.invoke_agent(agent_config, attempt_prompt_file, options=options)
            if _verbosity_rank(verbosity) >= _VERBOSITY_RANK[Verbosity.NORMAL]:
                _stream_parsed_agent_activity(
                    output_lines,
                    str(agent_config.json_parser),
                    effect.agent_name,
                    display,
                    raw_output_sink=raw_output,
                    rendered_output_sink=rendered_output,
                )
            else:
                raw_output.extend(str(line) for line in output_lines)
            return PipelineEvent.AGENT_SUCCESS
        except deps.agent_invocation_error as exc:
            recovery_plan = _build_agent_recovery_plan(
                exc=exc,
                attempt_index=attempt_index,
                max_recovery_attempts=max_recovery_attempts,
                effect=effect,
                workspace_root=workspace_scope.root,
                raw_output=raw_output,
                rendered_output=rendered_output,
                extracted_session_id=extract_session_id(raw_output),
                inactivity_error_type=AgentInactivityTimeoutError,
            )
            if recovery_plan is None:
                logger.error("Agent invocation failed: {}", exc)
                return PipelineEvent.AGENT_FAILURE
            logger.warning(
                "Retrying agent '{}' after {} ({}/{})",
                effect.agent_name,
                recovery_plan.reason,
                attempt_index + 1,
                max_recovery_attempts,
            )
            attempt_prompt_file = recovery_plan.prompt_file
            resume_session_id = recovery_plan.session_id
        except Exception:
            logger.exception("Unexpected error during agent invocation: {}")
            return PipelineEvent.AGENT_FAILURE
        finally:
            if bridge is not None:
                shutdown_mcp_server(bridge)
    return PipelineEvent.AGENT_FAILURE


def _same_agent_recovery_attempts(config: UnifiedConfig) -> int:
    raw = cast("object", getattr(config.general, "max_same_agent_retries", 1))
    return raw if isinstance(raw, int) and raw >= 0 else 1


def _agent_idle_timeout_seconds() -> float | None:
    raw = os.environ.get(_AGENT_IDLE_TIMEOUT_ENV)
    if raw is None:
        return _DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS
    try:
        parsed = float(raw)
    except ValueError:
        return _DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS
    return parsed if parsed > 0 else None


def _build_agent_recovery_plan(  # noqa: PLR0913
    *,
    exc: Exception,
    attempt_index: int,
    max_recovery_attempts: int,
    effect: InvokeAgentEffect,
    workspace_root: Path,
    raw_output: list[str],
    rendered_output: list[str],
    extracted_session_id: str | None,
    inactivity_error_type: type[Exception],
) -> _AgentRecoveryPlan | None:
    if attempt_index >= max_recovery_attempts:
        return None

    reason = _retryable_agent_failure_reason(exc, inactivity_error_type)
    if reason is None:
        return None

    if extracted_session_id:
        return _AgentRecoveryPlan(
            prompt_file=effect.prompt_file,
            session_id=extracted_session_id,
            reason=reason,
        )

    return _AgentRecoveryPlan(
        prompt_file=_write_agent_retry_prompt(
            workspace_root=workspace_root,
            prompt_file=effect.prompt_file,
            reason=reason,
            context_lines=_recovery_context_lines(exc, raw_output, rendered_output),
        ),
        session_id=None,
        reason=reason,
    )


def _retryable_agent_failure_reason(
    exc: Exception,
    inactivity_error_type: type[Exception],
) -> str | None:
    if isinstance(exc, inactivity_error_type):
        return "an inactivity timeout"

    details = "\n".join(_recovery_error_parts(exc)).lower()
    for marker in _TRANSIENT_CONNECTIVITY_MARKERS:
        if marker in details:
            return "a transient connectivity failure"
    return None


def _recovery_error_parts(exc: Exception) -> list[str]:
    parts: list[str] = [str(exc)]
    stderr = cast("object", getattr(exc, "stderr", None))
    if isinstance(stderr, str) and stderr.strip():
        parts.append(stderr.strip())
    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list):
        parts.extend(str(item).strip() for item in parsed_output if str(item).strip())
    return parts


def _recovery_context_lines(
    exc: Exception,
    raw_output: list[str],
    rendered_output: list[str],
) -> list[str]:
    if rendered_output:
        return rendered_output[-_RECOVERY_CONTEXT_LINES:]

    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list) and parsed_output:
        return [str(item) for item in parsed_output[-_RECOVERY_CONTEXT_LINES:]]

    stripped_raw = [line.strip() for line in raw_output if line.strip()]
    return stripped_raw[-_RECOVERY_CONTEXT_LINES:]


def _write_agent_retry_prompt(
    *,
    workspace_root: Path,
    prompt_file: str,
    reason: str,
    context_lines: list[str],
) -> str:
    prompt_path = Path(prompt_file)
    base_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    retry_prompt_path = prompt_dir / f"agent_retry_{uuid.uuid4().hex}.md"
    summary = "\n".join(context_lines) if context_lines else "(no output captured)"
    retry_prompt_path.write_text(
        (
            f"{base_prompt}\n\n"
            "RETRY CONTEXT:\n"
            f"The previous attempt ended because of {reason}.\n"
            "Treat this as an infrastructure interruption, not a new user request.\n"
            "Resume from the current workspace state instead of starting over.\n"
            "Review the latest files and prior output summary before continuing.\n"
            "Previous output summary:\n"
            f"{summary}\n"
        ),
        encoding="utf-8",
    )
    return str(retry_prompt_path)


def _clear_phase_output_artifacts(workspace: FsWorkspace, phase: str) -> None:
    """Remove stale per-phase artifacts before invoking an agent.

    This hardening makes phase handlers reason about outputs created by the
    current invocation instead of silently accepting artifacts left behind by a
    prior interrupted run. The mapping is intentionally explicit so new
    artifact-emitting phases must opt in deliberately.
    """
    for path in _phase_output_artifact_paths(phase):
        workspace.remove(path)


def _phase_output_artifact_paths(phase: str) -> tuple[str, ...]:
    phase_artifacts = {
        "development": (
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        ),
        "development_analysis": (
            ".agent/artifacts/development_analysis_decision.json",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        ),
        "review": (".agent/artifacts/issues.json", ".agent/ISSUES.md"),
        "review_analysis": (
            ".agent/artifacts/review_analysis_decision.json",
            ".agent/REVIEW_ANALYSIS_DECISION.md",
        ),
        "fix": (".agent/artifacts/fix_result.json", ".agent/FIX_RESULT.md"),
        "development_commit": (COMMIT_MESSAGE_ARTIFACT,),
        "review_commit": (COMMIT_MESSAGE_ARTIFACT,),
    }
    return phase_artifacts.get(phase, ())


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
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
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
        with suppress(Exception):
            render_commit_message(repo_root, _display_console(display))
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
) -> PipelineSubscriber | None:
    """Extract the pipeline subscriber from a display, when one is exposed."""
    if display is None or isinstance(display, _LegacyConsoleDisplay):
        return None
    if not hasattr(display, "subscriber"):
        return None
    return cast("PipelineSubscriber | None", display.subscriber)


def _record_activity_on_subscriber(
    subscriber: PipelineSubscriber,
    parsed_line: AgentOutputLine,
    rendered: Text | None,
    agent_name: str,
) -> None:
    try:
        line_text = "" if rendered is None else rendered.plain
        metadata = parsed_line.metadata
        tool_name: str | None = None
        metadata_tool = metadata.get("tool")
        if isinstance(metadata_tool, str) and metadata_tool.strip():
            tool_name = metadata_tool.strip()
        elif parsed_line.type == "tool_use":
            stripped = parsed_line.content.strip()
            if stripped:
                tool_name = stripped
        path = _format_metadata_value(metadata.get("path")) or None
        workdir = _format_metadata_value(metadata.get("workdir")) or None
        command = _format_metadata_value(metadata.get("command")) or None
        subscriber.record_activity(
            unit_id=agent_name,
            agent_name=agent_name,
            line=line_text,
            tool_name=tool_name,
            path=path,
            workdir=workdir,
            command=command,
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("subscriber.record_activity failed", exc_info=True)


def _stream_parsed_agent_activity(  # noqa: PLR0913
    lines: Iterable[object],
    parser_type: str,
    agent_name: str,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    *,
    raw_output_sink: list[str] | None = None,
    rendered_output_sink: list[str] | None = None,
) -> None:
    parser = _resolve_parser(parser_type)

    def _iter_lines() -> Iterator[str]:
        for line in lines:
            text = str(line)
            if raw_output_sink is not None:
                raw_output_sink.append(text)
            yield text

    subscriber = _subscriber_for_display(display)
    for parsed_line in parser.parse(_iter_lines()):
        rendered = _render_agent_activity_line(parsed_line, agent_name)
        if rendered is not None:
            if rendered_output_sink is not None:
                rendered_output_sink.append(rendered.plain)
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
    content_renderers: dict[str, Callable[[], Text | None]] = {
        "text": lambda: _render_text_line(agent_name, output.content, "white"),
        "assistant": lambda: _render_text_line(agent_name, output.content, "dim"),
        "result": lambda: _render_text_line(agent_name, output.content, "dim"),
        "tool_use": lambda: _render_tool_use_line(agent_name, output),
        "tool_result": lambda: _render_tool_result_line(agent_name, output.content),
        "error": lambda: _render_error_line(agent_name, output.content),
    }
    renderer = content_renderers.get(output.type)
    if renderer is not None:
        return renderer()
    return _render_metadata_event_line(agent_name, output)


def _render_text_line(agent_name: str, content: str, style: str) -> Text | None:
    stripped = content.strip()
    if not stripped:
        return None
    rendered = _styled_prefix(agent_name, style)
    text_width = min(_MAX_TEXT_LENGTH, _available_width(len(agent_name) + 2))
    rendered.append(_truncate(stripped, text_width))
    return rendered


def _render_tool_use_line(agent_name: str, output: AgentOutputLine) -> Text:
    tool_name = output.content.strip() or "unknown-tool"
    prefix_label = f"{agent_name} tool"
    rendered = _styled_prefix(prefix_label, "magenta")
    rendered.append(tool_name, style="bold magenta")
    input_summary = _tool_input_summary(output.metadata)
    if input_summary:
        prefix_total = len(prefix_label) + len(tool_name) + 4
        tool_input_width = min(_MAX_TOOL_INPUT_LENGTH, _available_width(prefix_total))
        truncated = _truncate(input_summary, tool_input_width)
        rendered.append(f" ({truncated})", style="dim")
    return rendered


def _render_tool_result_line(agent_name: str, content: str) -> Text | None:
    result = content.strip()
    if not result:
        return None
    result_label = f"{agent_name} result"
    rendered = _styled_prefix(result_label, "dim")
    result_prefix_len = len(result_label) + 2
    max_length = (
        _MAX_TOOL_RESULT_BRIEF
        if len(result) > _TOOL_RESULT_BRIEF_THRESHOLD
        else _MAX_TOOL_RESULT_LENGTH
    )
    result_width = min(max_length, _available_width(result_prefix_len))
    rendered.append(_truncate(result, result_width), style="dim")
    return rendered


def _render_error_line(agent_name: str, content: str) -> Text:
    error = content.strip() or "unknown error"
    rendered = _styled_prefix(f"{agent_name} ✗", "red")
    rendered.append(error, style="bold red")
    return rendered


def _render_metadata_event_line(agent_name: str, output: AgentOutputLine) -> Text:
    summary = _metadata_summary(output.metadata)
    rendered = _styled_prefix(agent_name, "dim")
    rendered.append(output.type, style="dim")
    if summary:
        rendered.append(f" ({summary})", style="dim")
    return rendered


def _tool_input_summary(metadata: dict[str, object]) -> str:
    if not metadata:
        return ""
    input_data = metadata.get("input")
    if not isinstance(input_data, dict):
        return ""
    args = input_data.get("args")
    if isinstance(args, str) and args:
        return args
    return _kv_summary(
        input_data,
        preferred_keys=("command", "workdir", "path", "file_path", "pattern", "name"),
        max_parts=_MAX_METADATA_PARTS,
        max_length=_MAX_TOOL_INPUT_LENGTH,
    )


def _metadata_summary(metadata: dict[str, object]) -> str:
    if not metadata:
        return ""
    return _kv_summary(
        metadata,
        preferred_keys=(
            "status",
            "summary",
            "phase",
            "decision",
            "message",
            "event",
            "tool",
            "path",
            "workdir",
            "command",
        ),
        max_parts=_MAX_METADATA_PARTS,
        max_length=_MAX_METADATA_SUMMARY_LENGTH,
    )


def _kv_summary(
    values: dict[str, object],
    *,
    preferred_keys: tuple[str, ...],
    max_parts: int,
    max_length: int,
) -> str:
    parts: list[str] = []
    for key in preferred_keys:
        value = _format_metadata_value(values.get(key))
        if value is None:
            continue
        parts.append(f"{key}={value}")
        if len(parts) >= max_parts:
            break
    return _truncate(", ".join(parts), max_length) if parts else ""


def _format_metadata_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    return None


def _styled_prefix(label: str, style: str) -> Text:
    """Create a styled prefix for activity lines."""
    text = Text()
    text.append(f"{label}: ", style=f"bold {style}")
    return text


def _status_text(label: str, value: str, style: str) -> Text:
    """Create a styled status text."""
    text = Text()
    text.append(f"{label}: ", style=f"bold {style}")
    text.append(value, style=style)
    return text


def _prompt_session_drain_for_phase(drain: str | None) -> SessionDrain:
    """Return the session drain to use for a phase."""
    if drain is not None:
        return SessionDrain(drain)
    return SessionDrain("cli")
