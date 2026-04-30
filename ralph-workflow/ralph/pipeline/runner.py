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
import threading
import time
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
from ralph.config.enums import Verbosity
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
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV
from ralph.mcp.protocol.session import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV, AgentSession
from ralph.mcp.server.lifecycle import shutdown_mcp_server, start_mcp_server
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.phases import PhaseContext, handle_phase, register_role_handlers
from ralph.phases.required_artifacts import (
    DEV_ANALYSIS_DECISION_JSON_PATH,
    DEV_RESULT_ARTIFACT_JSON_PATH,
    FIX_RESULT_ARTIFACT_JSON_PATH,
    ISSUES_ARTIFACT_JSON_PATH,
    REVIEW_ANALYSIS_DECISION_JSON_PATH,
)
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.cycle_baseline import (
    clear_cycle_baseline,
    read_cycle_baseline,
    write_cycle_baseline,
)
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
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent, WorkerFailedEvent
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
    from ralph.interrupt.asyncio_bridge import SignalBridge
    from ralph.mcp.upstream.agent_probe import AgentProbeReport
    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.mcp.upstream.validation import UpstreamValidationReport
    from ralph.pipeline.parallel import coordinator as parallel_coordinator
    from ralph.pipeline.work_units import WorkUnit
    from ralph.policy.models import AgentsPolicy, PhaseDefinition, PipelinePolicy, PolicyBundle
    from ralph.recovery.connectivity import ConnectivityState
    from ralph.recovery.controller import RecoveryController

    class _PipelineSubscriber(Protocol):
        def notify(self, state: PipelineState) -> None: ...


class _ConnectivityMonitorLike(Protocol):
    @property
    def current_state(self) -> ConnectivityState: ...

    def add_listener(
        self, cb: Callable[[object], None]
    ) -> Callable[[], None]: ...


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


class _PhaseAwareDisplay(Protocol):
    def begin_phase(self, phase: str) -> None: ...


class _RunEndDisplay(Protocol):
    def emit_run_end(
        self, *, phase: str, total_agent_calls: int, pr_url: str | None = None
    ) -> None: ...


console = Console()
class _SessionCapture(threading.local):
    session_id: str | None = None


_session_capture_local = _SessionCapture()
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
_POLICY_LOADER_CONFIG_ARITY = 2
_RECOVERY_CONTEXT_LINES = 12
_MIN_WORK_UNITS_FOR_PARALLEL_PREFLIGHT = 2
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
    if read_cycle_baseline(workspace_root) is not None:
        return
    try:
        repo = Repo(workspace_root)
    except InvalidGitRepositoryError:
        return
    if not repo.head.is_valid():
        return
    write_cycle_baseline(workspace_root, repo.head.commit.hexsha, force=True)


def _set_last_captured_session_id(session_id: str | None) -> None:
    _session_capture_local.session_id = session_id


def _pop_last_captured_session_id() -> str | None:
    session_id = _session_capture_local.session_id
    _session_capture_local.session_id = None
    return session_id


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
    display.emit(unit_id or "run", line.plain if isinstance(line, Text) else line)


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


def _reset_phase_chain_for_recovery(
    state: PipelineState,
    target_phase: str,
) -> PipelineState:
    """Reset the target phase chain when re-entering after the terminal failure route.

    Recovery from the failure route should restart the configured agent chain from its
    first agent. Without this reset, a recovered phase can resume on the last
    exhausted fallback agent and skip earlier agents on subsequent cycles.
    """
    chain = state.chain_for_phase(target_phase)
    if chain is None:
        return state

    return state.with_phase_chain(
        target_phase,
        AgentChainState(agents=chain.agents, current_index=0, retries=0),
    )


def _reduce_runtime_recovery(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    *,
    reason: str,
    recovery: RecoveryController | None = None,
    exc: BaseException | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if recovery is not None:
        # Pass the raw exception so the classifier sees the exception type, not just a
        # string reason. This preserves AgentInvocationError → AGENT classification.
        raw_failure: BaseException | str = exc if exc is not None else reason
        new_state, effects, _ = recovery.handle(
            state,
            raw_failure,
            phase=state.phase,
            agent=state.current_agent(),
        )
        if state.work_units and not new_state.work_units:
            new_state = new_state.copy_with(work_units=state.work_units)
        return new_state, effects
    failure_event = PhaseFailureEvent(
        phase=state.phase,
        reason=reason,
        recoverable=True,
    )
    recovered_state, effects = reducer_reduce(
        state, failure_event, pipeline_policy, recovery=None
    )
    return recovered_state, effects


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
    recovery_controller: RecoveryController | None = None,
    _monitor_stop_cb: Callable[[], None] | None = None,
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
                _monitor_stop_cb=_monitor_stop_cb,
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
            if isinstance(effect, InvokeAgentEffect):
                captured_session_id = _pop_last_captured_session_id()
                if captured_session_id:
                    state = state.copy_with(
                        last_agent_session_id=captured_session_id,
                        session_preserve_retry_pending=False,
                    )
                elif state.session_preserve_retry_pending is True:
                    state = state.copy_with(session_preserve_retry_pending=False)
            if isinstance(effect, InvokeAgentEffect) and event == PipelineEvent.AGENT_SUCCESS:
                if recovery_controller is not None:
                    recovery_controller.reset_backoff(effect.phase, effect.agent_name)
                event = _phase_event_after_agent_run(
                    effect=effect,
                    config=config,
                    policy_bundle=policy_bundle,
                    workspace=workspace,
                    workspace_scope=workspace_scope,
                    display=display,
                    verbosity=verbosity,
                )

        _commit_phase_def = policy_bundle.pipeline.phases.get(state.phase)
        if (
            isinstance(effect, CommitEffect)
            and _commit_phase_def is not None
            and _commit_phase_def.role == "commit"
            and event in (PipelineEvent.COMMIT_SUCCESS, PipelineEvent.COMMIT_SKIPPED)
        ):
            clear_cycle_baseline(workspace_scope.root)
        next_state, _ = reducer_reduce(state, event, policy_bundle.pipeline)
        _notify_pipeline_subscriber(pipeline_subscriber, next_state)
        _save_checkpoint_or_log(
            next_state,
            message=(
                "Checkpoint save failed in phase={phase}: {err} -- continuing without checkpoint"
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
        recovered_state, _recv_effects = _reduce_runtime_recovery(
            state,
            policy_bundle.pipeline,
            reason=f"Pipeline step crashed: {type(exc).__name__}: {exc}",
            recovery=recovery_controller,
            exc=exc,
        )
        for _eff in _recv_effects:
            if isinstance(_eff, ExitFailureEffect):
                _emit_display_line(
                    display, None, _status_text("Recovery exhausted", _eff.reason, "red")
                )
                return 1
        _notify_pipeline_subscriber(pipeline_subscriber, recovered_state)
        _save_checkpoint_or_log(
            recovered_state,
            message="Checkpoint save failed while recording recovery in phase={phase}: {err}",
        )
        return recovered_state


def _phase_context(
    state: PipelineState,
    previous_phase: str,
    pipeline_policy: PipelinePolicy,
) -> dict[str, object]:
    """Build a context dict for emit_phase_transition with iteration/decision hints."""
    context: dict[str, object] = {}
    current_phase_def = pipeline_policy.phases.get(state.phase)
    previous_phase_def = pipeline_policy.phases.get(previous_phase)

    current_role = current_phase_def.role if current_phase_def is not None else None
    previous_role = previous_phase_def.role if previous_phase_def is not None else None

    if current_role == "execution":
        context["iteration"] = f"{state.iteration + 1}/{state.total_iterations}"
    if current_role == "review":
        context["pass"] = f"{state.reviewer_pass + 1}/{state.total_reviewer_passes}"
    if previous_role == "analysis":
        if current_role == "commit":
            context["decision"] = "approved"
        elif current_role == "execution":
            context["decision"] = "needs changes"
    if previous_role == "commit" and previous_phase_def is not None:
        commit_policy = previous_phase_def.commit_policy
        if commit_policy is not None and commit_policy.increments_counter:
            counter = commit_policy.increments_counter
            remaining = state.get_budget_remaining(counter)
            context[f"{counter}_budget"] = f"{remaining} remaining"
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
    pipeline_policy: PipelinePolicy,
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
    context = _phase_context(state, previous_phase, pipeline_policy) or None
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


def _load_policy_bundle_for_run(
    workspace_root: Path,
    config: UnifiedConfig,
) -> PolicyBundle:
    loader = load_policy_or_die
    params = signature(loader).parameters
    if "config" in params:
        return loader(workspace_root / ".agent", config=config)

    positional = [
        param
        for param in params.values()
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    if (
        any(param.kind == param.VAR_KEYWORD for param in params.values())
        or len(positional) >= _POLICY_LOADER_CONFIG_ARITY
    ):
        return loader(workspace_root / ".agent", config=config)
    return loader(workspace_root / ".agent")


def run(  # noqa: PLR0912, PLR0913, PLR0915
    config: UnifiedConfig,
    initial_state: PipelineState | None = None,
    display: ParallelDisplay | None = None,
    pipeline_subscriber: _PipelineSubscriber | None = None,
    *,
    dashboard_subscriber: _PipelineSubscriber | None = None,
    verbosity: Verbosity | None = None,
    connectivity_monitor: _ConnectivityMonitorLike | None = None,
    _recovery_sleep: Callable[[float], None] | None = None,
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
    policy_bundle = _load_policy_bundle_for_run(workspace_scope.root, config)
    register_role_handlers(policy_bundle.pipeline)
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

    from ralph.recovery.budget import seed_budget_registry as _seed_budget_registry  # noqa: PLC0415
    from ralph.recovery.controller import RecoveryController as _RecoveryController  # noqa: PLC0415
    from ralph.recovery.events import FailureEvent as _FailureEvent  # noqa: PLC0415
    from ralph.recovery.events import FalloverEvent as _FalloverEvent  # noqa: PLC0415
    _sleep = _recovery_sleep or time.sleep
    _cycle_cap: int = 200
    _raw_cycle_cap: object = getattr(state, "recovery_cycle_cap", 200)
    if isinstance(_raw_cycle_cap, int) and _raw_cycle_cap >= 1:
        _cycle_cap = _raw_cycle_cap
    _monitor_stop: Callable[[], None] | None = None
    if connectivity_monitor is None:
        import asyncio as _asyncio_mon  # noqa: PLC0415

        from ralph.recovery.connectivity import ConnectivityMonitor as _ConnMon  # noqa: PLC0415
        _real_monitor = _ConnMon()
        connectivity_monitor = _real_monitor
        _mon_loop = _asyncio_mon.new_event_loop()
        _mon_thread_started = threading.Event()

        def _run_mon_thread() -> None:
            _asyncio_mon.set_event_loop(_mon_loop)
            _mon_loop.run_until_complete(_real_monitor.start())
            _mon_thread_started.set()
            _mon_loop.run_forever()
            _mon_loop.close()

        _mon_thread = threading.Thread(
            target=_run_mon_thread, daemon=True, name="connectivity-probe"
        )
        _mon_thread.start()

        def _stop_mon() -> None:
            import asyncio as _asyncio_stop  # noqa: PLC0415
            future = _asyncio_stop.run_coroutine_threadsafe(_real_monitor.stop(), _mon_loop)
            with suppress(Exception):
                future.result(timeout=2.0)
            _mon_loop.call_soon_threadsafe(_mon_loop.stop)
            _mon_thread.join(timeout=3.0)

        _monitor_stop = _stop_mon
    _controller = _RecoveryController(
        cycle_cap=_cycle_cap,
        policy_bundle=policy_bundle,
        budget_registry=_seed_budget_registry(policy_bundle),
    )

    def _log_recovery_event(evt: object) -> None:
        if isinstance(evt, _FailureEvent):
            # Get remaining budget for this phase/agent from the controller snapshot
            remaining: int | None = None
            if evt.agent:
                snap = _controller.snapshot()
                budgets = snap.get("budgets")
                if isinstance(budgets, dict):
                    key = f"{evt.phase}:{evt.agent}"
                    budget_info = budgets.get(key)
                    if isinstance(budget_info, dict):
                        remaining = budget_info.get("remaining")
            logger.bind(recovery=True).info(
                "FAILURE phase={} agent={} category={} counted={}"
                " chain_cap={} cycle={} delay_ms={} remaining={}",
                evt.phase, evt.agent, evt.category, evt.counted_against_budget,
                evt.chain_capacity_remaining, evt.recovery_cycle, evt.retry_delay_ms,
                remaining,
            )
        elif isinstance(evt, _FalloverEvent):
            logger.bind(recovery=True).info(
                "FALLOVER phase={} from={} to={} reason={}",
                evt.phase, evt.from_agent, evt.to_agent, evt.reason,
            )

    _unsubscribe_bus = _controller.event_bus.subscribe(_log_recovery_event)

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
            if not is_quiet and hasattr(active_display, "emit_run_start"):
                with suppress(Exception):
                    from ralph.display.plain_renderer import RunStartOrientation  # noqa: PLC0415

                    _prompt_path: str | None = None
                    if effective_pipeline_subscriber is not None:
                        _prompt_path = getattr(effective_pipeline_subscriber, "_prompt_path", None)
                    _dev_phase = policy_bundle.pipeline.phases.get("development")
                    _dev_para = _dev_phase.parallelization if _dev_phase is not None else None
                    _parallel_max_workers: int | None = (
                        _dev_para.max_parallel_workers if _dev_para is not None else None
                    )
                    _plan_present = (
                        workspace_scope.root / ".agent" / "artifacts" / "plan.json"
                    ).exists()
                    _orientation = RunStartOrientation(
                        prompt_path=_prompt_path,
                        developer_agent=getattr(config, "developer_agent", None),  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                        developer_model=getattr(config, "developer_model", None),  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                        reviewer_agent=getattr(config, "reviewer_agent", None),  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                        reviewer_model=getattr(config, "reviewer_model", None),  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                        developer_iters=config.general.developer_iters,
                        reviewer_reviews=config.general.reviewer_reviews,
                        parallel_max_workers=_parallel_max_workers,
                        plan_present=_plan_present,
                        verbosity=str(effective_verbosity.value)
                        if hasattr(effective_verbosity, "value")
                        else str(effective_verbosity),
                        workspace_root=str(workspace_scope.root),
                    )
                    cast("ParallelDisplay", active_display).emit_run_start(_orientation)
            if hasattr(active_display, "begin_phase"):
                with suppress(Exception):
                    cast("_PhaseAwareDisplay", active_display).begin_phase(state.phase)
            _notify_pipeline_subscriber(effective_pipeline_subscriber, state)
            try:
                while state.phase != policy_bundle.pipeline.terminal_phase:
                    if connectivity_monitor is not None:
                        state = _apply_connectivity_check(state, connectivity_monitor)
                    step_result = _run_pipeline_step(
                        state=state,
                        policy_bundle=policy_bundle,
                        workspace_scope=workspace_scope,
                        config=config,
                        display=active_display,
                        verbosity=effective_verbosity,
                        registry=registry,
                        pipeline_subscriber=effective_pipeline_subscriber,
                        recovery_controller=_controller,
                        _monitor_stop_cb=_monitor_stop,
                    )
                    if isinstance(step_result, int):
                        return step_result
                    state = step_result
                    delay_ms = state.last_retry_delay_ms
                    if isinstance(delay_ms, int) and delay_ms > 0:
                        state = state.copy_with(last_retry_delay_ms=0)
                        _sleep(delay_ms / 1000.0)
                    _prev_phase = _emit_phase_transition_if_changed(
                        active_display,
                        _prev_phase,
                        state,
                        verbosity=effective_verbosity,
                        pipeline_policy=policy_bundle.pipeline,
                    )
                    if hasattr(active_display, "begin_phase"):
                        with suppress(Exception):
                            cast("_PhaseAwareDisplay", active_display).begin_phase(state.phase)

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

            if state.phase == policy_bundle.pipeline.terminal_phase:
                active_display.emit("run", "[green]Pipeline completed successfully.[/green]")
                exit_code = 0
            else:
                _emit_display_line(
                    active_display,
                    None,
                    _status_text("Pipeline failed", state.last_error or "Unknown error", "red"),
                )
                exit_code = 1
            if not is_quiet and hasattr(active_display, "emit_run_end"):
                with suppress(Exception):
                    total_agent_calls = getattr(state.metrics, "total_agent_calls", 0)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    cast("_RunEndDisplay", active_display).emit_run_end(
                        phase=state.phase,
                        total_agent_calls=total_agent_calls,  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                        pr_url=state.pr_url,
                    )
    finally:
        with suppress(Exception):
            _unsubscribe_bus()
        if _monitor_stop is not None:
            with suppress(Exception):
                _monitor_stop()
        _emit_final_summary(
            state,
            workspace_scope.root,
            subscriber=cast("PipelineSubscriber | None", effective_pipeline_subscriber),
        )
        with suppress(Exception):
            clear_cycle_baseline(workspace_scope.root)
    return exit_code



def _apply_connectivity_check(
    state: PipelineState, monitor: _ConnectivityMonitorLike
) -> PipelineState:
    """Block synchronously if offline; return updated state when online.

    Uses a threading.Event driven by monitor.add_listener() so this works in the
    synchronous runner without an event loop; compatible with FakeConnectivityMonitor
    (tests) and ConnectivityMonitor (production, started externally).

    Records last_connectivity_state='offline' before blocking so checkpoints written
    during an interrupt while paused accurately reflect the offline condition.
    """
    from ralph.recovery.connectivity import ConnectivityState  # noqa: PLC0415

    if monitor.current_state != ConnectivityState.OFFLINE:
        return state

    logger.bind(recovery=True).warning(
        "Pipeline paused: network offline, waiting for connectivity to restore..."
    )
    # Record offline before blocking so any checkpoint saved during the wait reflects reality.
    offline_state = state.copy_with(last_connectivity_state=str(ConnectivityState.OFFLINE))
    wake = threading.Event()

    def _on_transition(evt: object) -> None:
        from ralph.recovery.connectivity import ConnectivityEvent  # noqa: PLC0415
        from ralph.recovery.connectivity import ConnectivityState as _ConnState  # noqa: PLC0415

        if isinstance(evt, ConnectivityEvent) and evt.state == _ConnState.ONLINE:
            wake.set()

    unsub = monitor.add_listener(_on_transition)
    try:
        # Capture state AFTER listener registration to handle the race where
        # state changed to ONLINE between the top-of-function check and now.
        # Mypy flags this as unreachable because it sees current_state as
        # invariant, but in FakeConnectivityMonitor (test) another thread can
        # call go_online() between checks. This is a genuine race guard.
        _was_online_at_registration = monitor.current_state != ConnectivityState.OFFLINE
        if _was_online_at_registration:
            wake.set()
        else:
            wake.wait()
    finally:
        unsub()

    logger.bind(recovery=True).info("Connectivity restored, resuming pipeline")
    return offline_state.copy_with(last_connectivity_state=str(ConnectivityState.ONLINE))


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
    bridge: SignalBridge,
) -> tuple[AgentExecutor, parallel_coordinator._WorkerContext]:
    from ralph.agents.subprocess_executor import SubprocessAgentExecutor  # noqa: PLC0415
    from ralph.mcp.server.factory_impl import DynamicBindingMcpServerFactory  # noqa: PLC0415
    from ralph.pipeline.parallel import coordinator  # noqa: PLC0415
    from ralph.pipeline.parallel.mode import SameWorkspaceContext  # noqa: PLC0415

    executor = cast(
        "AgentExecutor",
        SubprocessAgentExecutor(_parallel_worker_command(), signal_bridge=bridge),
    )
    workspace = FsWorkspace(
        workspace_scope.root,
        allowed_roots=workspace_scope.allowed_roots,
    )
    worker_namespace_root = repo_root / ".agent" / "workers"
    worker_namespace_root.mkdir(parents=True, exist_ok=True)
    return executor, coordinator._WorkerContext(
        log=coordinator._WorkerLog(
            log_dir=workspace_scope.root / ".agent" / "logs",
            run_id=str(uuid.uuid4()),
        ),
        same_workspace=SameWorkspaceContext(
            repo_root=repo_root,
            mcp_factory=DynamicBindingMcpServerFactory(workspace=workspace),
            executor_command=_parallel_worker_command(),
            signal_bridge=bridge,
            worker_namespace_root=worker_namespace_root,
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


def _write_parallel_development_summary(  # noqa: PLR0913
    workspace_scope: WorkspaceScope,
    effect: FanOutDevelopmentEffect,
    state: PipelineState,
    *,
    verify_ran: bool,
    verify_passed: bool | None,
    verify_exit_code: int | None,
) -> None:
    """Write .agent/artifacts/parallel_development_summary.json after fan-out completes.

    The summary records per-worker status, artifact counts, and verification results.
    It is the authoritative handoff for the analysis phase when fan-out was used.
    Worker success is based on per-worker artifact evidence, never repo-wide git state.
    """
    import json  # noqa: PLC0415

    from ralph.mcp.artifacts.store import list_artifacts  # noqa: PLC0415
    from ralph.pipeline.worker_state import WorkerStatus  # noqa: PLC0415

    workers: list[dict[str, object]] = []
    for unit in effect.work_units:
        uid = unit.unit_id
        ws = state.worker_states.get(uid)
        artifact_dir = workspace_scope.root / ".agent" / "workers" / uid / "artifacts"
        artifact_count = len(list_artifacts(artifact_dir)) if artifact_dir.exists() else 0

        if ws is None:
            status = "failed"
            final_message = "Worker state not recorded"
        elif ws.status == WorkerStatus.SUCCEEDED:
            status = "succeeded"
            final_message = None
        elif ws.status == WorkerStatus.CANCELLED:
            status = "cancelled"
            final_message = ws.error_message
        elif ws.status == WorkerStatus.FAILED:
            err = ws.error_message or ""
            status = "blocked" if err.startswith("Blocked by") else "failed"
            final_message = ws.error_message
        else:
            status = "failed"
            final_message = ws.error_message

        workers.append({
            "unit_id": uid,
            "status": status,
            "artifact_count": artifact_count,
            "final_message": final_message,
        })

    any_failed = any(w["status"] in ("failed", "cancelled", "blocked") for w in workers)
    all_succeeded = not any_failed and len(workers) > 0

    if verify_ran and not verify_passed:
        workers.append({
            "unit_id": "__verify__",
            "status": "failed",
            "artifact_count": 0,
            "final_message": "workspace verification failed",
        })
        any_failed = True
        all_succeeded = False

    summary: dict[str, object] = {
        "workers": workers,
        "any_failed": any_failed,
        "all_succeeded": all_succeeded,
        "verification": {
            "ran": verify_ran,
            "passed": verify_passed,
            "exit_code": verify_exit_code,
        },
    }

    agent_artifacts = workspace_scope.root / ".agent" / "artifacts"
    summary_path = agent_artifacts / "parallel_development_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.debug(
        "Wrote parallel_development_summary.json: any_failed={f} all_succeeded={s}",
        f=any_failed,
        s=all_succeeded,
    )

    from ralph.mcp.artifacts.handoffs import sync_markdown_handoff  # noqa: PLC0415

    sync_markdown_handoff(workspace_scope.root, "parallel_development_summary", summary)


async def _run_post_fanout_verification(workspace_scope: WorkspaceScope) -> str | None:
    """Run workspace-wide verification exactly once after all workers complete.

    This helper runs on the runner thread (serialized) after coordinator.run_fan_out
    returns. It is called only when all workers succeeded. Never spawns parallel
    verifications. NOT related to git worktree isolation.

    Returns:
        None on success, or an error description string on failure.
    """
    from ralph.executor.process import run_process_async  # noqa: PLC0415

    logger.debug("Running post-fanout workspace-wide verification (serialized)")
    verify_result = await run_process_async(
        "make",
        ["-C", str(workspace_scope.root / "ralph-workflow"), "verify"],
    )
    if verify_result.returncode != 0:
        return (
            f"Post-fanout workspace verification failed "
            f"(exit {verify_result.returncode}): "
            f"{verify_result.stderr.strip() or verify_result.stdout.strip()}"
        )
    return None


async def _run_fan_out_async(  # noqa: PLR0913, PLR0915
    *,
    effect: FanOutDevelopmentEffect,
    state: PipelineState,
    display: ParallelDisplay,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    repo_root: Path,
    pipeline_subscriber: _PipelineSubscriber | None,
    _monitor_stop_cb: Callable[[], None] | None = None,
) -> PipelineState:
    import asyncio  # noqa: PLC0415

    from ralph.interrupt.asyncio_bridge import (  # noqa: PLC0415
        SignalBridge,
        install_signal_handlers,
    )
    from ralph.pipeline.events import PostFanoutVerificationEvent  # noqa: PLC0415
    from ralph.pipeline.parallel import coordinator  # noqa: PLC0415
    from ralph.pipeline.work_units import (  # noqa: PLC0415
        WorkUnitsPlan,
        WorkUnitsValidationError,
        validate_for_same_workspace,
    )

    current = state
    try:
        loop = asyncio.get_running_loop()
        bridge = SignalBridge()
        if _monitor_stop_cb is not None:
            bridge._connectivity_stop = _monitor_stop_cb
        root_task = cast("asyncio.Task[object] | None", asyncio.current_task())
        assert root_task is not None
        install_signal_handlers(loop, root_task, bridge)

        # Pre-flight: validate plan is safe for same-workspace execution.
        try:
            validate_for_same_workspace(WorkUnitsPlan(work_units=list(effect.work_units)))
        except WorkUnitsValidationError as exc:
            failure_reason = (
                f"Parallel plan rejected (same-workspace safety check failed): {exc}"
            )
            logger.error(failure_reason)
            recovered, _ = _reduce_runtime_recovery(
                current,
                policy_bundle.pipeline,
                reason=failure_reason,
            )
            _notify_pipeline_subscriber(pipeline_subscriber, recovered)
            _save_checkpoint_or_log(
                recovered,
                message="Checkpoint save failed after plan rejection in phase={phase}: {err}",
            )
            return recovered

        executor, worker_ctx = _fan_out_worker_context(
            workspace_scope=workspace_scope,
            repo_root=repo_root,
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

        any_worker_failed = any(isinstance(ev, WorkerFailedEvent) for ev in fan_out_events)
        verify_ran = False
        verify_passed: bool | None = None
        verify_exit_code: int | None = None
        if effect.run_post_fanout_verification and not any_worker_failed:
            verify_ran = True
            verify_error = await _run_post_fanout_verification(workspace_scope)
            if verify_error is not None:
                logger.error(verify_error)
                verify_passed = False
                verify_exit_code = 1
                verify_ev = PostFanoutVerificationEvent(
                    success=False, exit_code=verify_exit_code, error=verify_error
                )
            else:
                verify_passed = True
                verify_exit_code = 0
                verify_ev = PostFanoutVerificationEvent(success=True, exit_code=0)
            current, _ = reducer_reduce(current, verify_ev, policy_bundle.pipeline)
            _notify_pipeline_subscriber(pipeline_subscriber, current)
            _save_checkpoint_or_log(
                current,
                message=(
                    "Checkpoint save failed after verification in phase={phase}: {err}"
                ),
            )
            if not verify_passed:
                _write_parallel_development_summary(
                    workspace_scope,
                    effect,
                    current,
                    verify_ran=verify_ran,
                    verify_passed=verify_passed,
                    verify_exit_code=verify_exit_code,
                )
                return current
        elif effect.run_post_fanout_verification and any_worker_failed:
            logger.debug(
                "Post-fanout verification skipped: one or more workers failed in this wave"
            )

        _write_parallel_development_summary(
            workspace_scope,
            effect,
            current,
            verify_ran=verify_ran,
            verify_passed=verify_passed,
            verify_exit_code=verify_exit_code,
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
        recovered, _ = _reduce_runtime_recovery(
            current,
            policy_bundle.pipeline,
            reason=f"Fan-out execution crashed: {type(exc).__name__}: {exc}",
        )
        _notify_pipeline_subscriber(pipeline_subscriber, recovered)
        _save_checkpoint_or_log(
            recovered,
            message=(
                "Checkpoint save failed while recording fan-out recovery in phase={phase}: {err}"
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
    _monitor_stop_cb: Callable[[], None] | None = None,
) -> PipelineState:
    """Execute fan-out development synchronously by wrapping asyncio.run()."""
    import asyncio  # noqa: PLC0415

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
            pipeline_subscriber=effective_pipeline_subscriber,
            _monitor_stop_cb=_monitor_stop_cb,
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
        prepared_state = state
        if state.phase == pipeline_policy.recovery.failed_route:
            prepared_state = _reset_phase_chain_for_recovery(state, effect.phase)
            target_phase_def = pipeline_policy.phases.get(effect.phase)
            if target_phase_def is not None and target_phase_def.role == "execution":
                clear_cycle_baseline(workspace_scope.root)
                _write_start_commit_if_absent(workspace_scope.root)
        updated_state = prepared_state.copy_with(
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
            phase=pipeline_policy.recovery.failed_route,
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
    import os  # noqa: PLC0415

    workspace = FsWorkspace(
        workspace_scope.root,
        allowed_roots=workspace_scope.allowed_roots,
    )
    worker_ns_str = os.environ.get("RALPH_WORKER_NAMESPACE")
    worker_namespace = Path(worker_ns_str) if worker_ns_str else None
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
        worker_namespace=worker_namespace,
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


def _initial_phase_chains(
    config: UnifiedConfig,
    *,
    agents_policy: AgentsPolicy | None,
    pipeline_policy: PipelinePolicy | None,
) -> dict[str, AgentChainState]:
    if pipeline_policy is None:
        return {}
    return {
        phase_name: AgentChainState(
            agents=_agents_for_phase(
                config,
                phase_name,
                agents_policy=agents_policy,
                pipeline_policy=pipeline_policy,
            )
        )
        for phase_name in pipeline_policy.phases
    }


def _create_initial_state(
    config: UnifiedConfig,
    *,
    agents_policy: AgentsPolicy | None = None,
    pipeline_policy: PipelinePolicy,
) -> PipelineState:
    """Create initial pipeline state from configuration.

    Args:
        config: Unified configuration.
        pipeline_policy: Pipeline policy (required for entry_phase resolution).

    Returns:
        Initial PipelineState.
    """
    entry_phase = pipeline_policy.entry_phase
    phase_chains = _initial_phase_chains(
        config,
        agents_policy=agents_policy,
        pipeline_policy=pipeline_policy,
    )

    return PipelineState(
        phase=entry_phase,
        total_iterations=config.general.developer_iters,
        total_reviewer_passes=config.general.reviewer_reviews,
        development_budget_remaining=config.general.developer_iters,
        review_budget_remaining=config.general.reviewer_reviews,
        phase_chains=phase_chains,
        rebase=RebaseState(),
        commit=CommitState(),
        policy_entry_phase=entry_phase,
        current_drain=resolve_phase_drain(entry_phase, pipeline_policy),
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


def _recovery_prepare_effect(
    state: PipelineState, pipeline_policy: PipelinePolicy
) -> PreparePromptEffect:
    previous_phase = state.previous_phase if isinstance(state.previous_phase, str) else None
    failed_route = pipeline_policy.recovery.failed_route
    policy_entry_phase = (
        state.policy_entry_phase
        if isinstance(state.policy_entry_phase, str)
        else pipeline_policy.entry_phase
    )
    target_phase = previous_phase or policy_entry_phase
    if target_phase == failed_route:
        target_phase = policy_entry_phase
    drain = state.current_drain if isinstance(state.current_drain, str) else None
    return PreparePromptEffect(
        phase=target_phase,
        iteration=state.iteration,
        drain=drain,
    )


def _terminal_phase_effect(state: PipelineState, pipeline_policy: PipelinePolicy) -> Effect | None:
    if state.phase == pipeline_policy.terminal_phase:
        return ExitSuccessEffect()
    if state.phase == pipeline_policy.recovery.failed_route:
        return _recovery_prepare_effect(state, pipeline_policy)
    return None


def _determine_effect_from_policy(  # noqa: PLR0911
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope | None = None,
    *,
    config: UnifiedConfig | None = None,
) -> Effect:
    terminal = _terminal_phase_effect(state, policy_bundle.pipeline)
    if terminal is not None:
        return terminal

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return ExitFailureEffect(reason=f"Unknown phase: {state.phase}")

    if phase_def.role == "commit":
        scope = workspace_scope or resolve_workspace_scope()
        return _commit_phase_effect(state, policy_bundle, phase_def, scope, config=config)

    if len(state.work_units) >= 2:  # noqa: PLR2004
        phase_para = phase_def.parallelization
        if phase_para is None:
            return ExitFailureEffect(
                reason=(
                    f"Phase {state.phase!r} does not declare parallelization but the plan "
                    f"declares {len(state.work_units)} work_units; either declare "
                    f"[phases.{state.phase}.parallelization] or remove the work_units from the plan"
                )
            )
        from ralph.pipeline.work_units import (  # noqa: PLC0415
            WorkUnitsPlan,
            WorkUnitsValidationError,
            validate_for_same_workspace,
        )

        try:
            validate_for_same_workspace(WorkUnitsPlan(work_units=list(state.work_units)))
        except WorkUnitsValidationError as exc:
            offending = ", ".join(
                u.unit_id for u in state.work_units if not u.allowed_directories
            ) or "(see details)"
            return ExitFailureEffect(
                reason=f"parallel preflight rejected plan: {exc} (offending units: {offending})"
            )
        return FanOutDevelopmentEffect(
            work_units=state.work_units,
            max_workers=phase_para.max_parallel_workers,
            run_post_fanout_verification=phase_para.post_fanout_verification,
        )

    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle, config=config)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for phase '{state.phase}'")

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

    if agents_policy is None or pipeline_policy is None:
        return []

    phase_def = pipeline_policy.phases.get(phase)
    if phase_def is None:
        return []

    drain_binding = agents_policy.agent_drains.get(phase_def.drain)
    if drain_binding is None:
        return []

    chain_config = agents_policy.agent_chains.get(drain_binding.chain)
    if chain_config is None:
        return []

    return list(chain_config.agents)


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

    drain_binding = policy_bundle.agents.agent_drains.get(phase_def.drain)
    if drain_binding is None:
        return None

    chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None or not chain_config.agents:
        return None

    return chain_config.agents[0]


def _phase_event_after_agent_run(  # noqa: PLR0913
    *,
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    workspace: FsWorkspace,
    workspace_scope: WorkspaceScope | None = None,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
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
            verbosity=verbosity,
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


def _render_phase_artifact_handoff(  # noqa: PLR0912
    phase: str,
    event: Event,
    workspace_root: Path,
    display: ParallelDisplay | _LegacyConsoleDisplay | None,
    *,
    verbosity: Verbosity = Verbosity.VERBOSE,
) -> None:
    console_obj = _display_console(display)

    if phase == "planning" and event == PipelineEvent.AGENT_SUCCESS:
        render_plan_artifact(workspace_root, console_obj)
        if verbosity != Verbosity.QUIET and hasattr(display, "emit_phase_close"):
            with suppress(Exception):
                from ralph.display.artifact_reader import read_plan_artifact  # noqa: PLC0415

                plan = read_plan_artifact(workspace_root)
                if plan is not None:
                    produced = (
                        f"plan: {plan.total_steps} step(s), {len(plan.risks_mitigations)} risk(s)"
                    )
                else:
                    produced = "plan: (no plan artifact on disk)"
                cast("ParallelDisplay", display).emit_phase_close(phase, produced)
        return
    if phase == "development" and event == PipelineEvent.AGENT_SUCCESS:
        render_development_artifact(workspace_root, console_obj)
        if verbosity != Verbosity.QUIET and hasattr(display, "emit_phase_close"):
            with suppress(Exception):
                dev_result_path = workspace_root / DEV_RESULT_ARTIFACT_JSON_PATH
                produced = (
                    "development: result artifact present"
                    if dev_result_path.exists()
                    else "development: no result artifact"
                )
                cast("ParallelDisplay", display).emit_phase_close(phase, produced)
        return
    if phase == "review" and event == PipelineEvent.AGENT_SUCCESS:
        render_review_artifact(workspace_root, console_obj)
        if verbosity != Verbosity.QUIET and hasattr(display, "emit_phase_close"):
            with suppress(Exception):
                import json  # noqa: PLC0415

                issues_path = workspace_root / ".agent" / "artifacts" / "issues.json"
                issue_count = 0
                if issues_path.exists():
                    try:
                        issues_data = json.loads(issues_path.read_text(encoding="utf-8"))  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                        content_obj = (
                            issues_data.get("content")
                            if isinstance(issues_data, dict)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                            else issues_data  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                        )
                        issues_list = (
                            content_obj.get("issues")
                            if isinstance(content_obj, dict)
                            else content_obj
                        )
                        if isinstance(issues_list, list):
                            issue_count = len(issues_list)
                    except Exception:
                        pass
                cast("ParallelDisplay", display).emit_phase_close(
                    phase, f"review: {issue_count} issue(s)"
                )
        return
    if phase == "fix" and event == PipelineEvent.AGENT_SUCCESS:
        render_fix_artifact(workspace_root, console_obj)
        if verbosity != Verbosity.QUIET and hasattr(display, "emit_phase_close"):
            with suppress(Exception):
                cast("ParallelDisplay", display).emit_phase_close(phase, "fix: applied")
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
            effect,
            create_commit,
            stage_all,
            workspace_scope.root,
            display,
            verbosity=verbosity,
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
    resume_session_id: str | None = (
        state.last_agent_session_id
        if (
            state is not None
            and state.session_preserve_retry_pending
            and state.last_agent_session_id
        )
        else None
    )
    max_recovery_attempts = _same_agent_recovery_attempts(config)

    for attempt_index in range(max_recovery_attempts + 1):
        bridge = None
        raw_output: list[str] = []
        rendered_output: list[str] = []
        try:
            system_prompt_file = materialize_system_prompt(
                workspace_root=workspace_scope.root,
                name=str(effect.phase),
            )
            session_mcp_plan = build_session_mcp_plan(
                transport=agent_config.transport,
                drain=effect.drain or effect.phase,
                workspace_path=workspace_scope.root,
            )
            session = AgentSession(
                session_id=f"{effect.phase}-{uuid.uuid4().hex[:8]}",
                run_id=str(uuid.uuid4()),
                drain=effect.drain or effect.phase,
                capabilities=set(session_mcp_plan.capabilities),
            )
            workspace = FsWorkspace(
                workspace_scope.root,
                allowed_roots=workspace_scope.allowed_roots,
            )
            _clear_phase_output_artifacts(
                workspace,
                effect.phase,
                drain=effect.drain,
            )
            bridge = start_mcp_server(
                session,
                workspace,
                phase=effect.phase,
                extra_env=session_mcp_plan.server_env,
            )

            options = InvokeOptions(
                verbose=config.general.verbosity >= _VERBOSE_LOG_LEVEL,
                show_progress=False,
                workspace_path=workspace_scope.root,
                extra_env={
                    MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri(),
                    MCP_RUN_ID_ENV: session.run_id,
                    AGENT_LABEL_SCOPE_ENV: session.run_id,
                },
                idle_timeout_seconds=config.general.agent_idle_timeout_seconds,
                drain_window_seconds=config.general.agent_idle_drain_window_seconds,
                max_waiting_on_child_seconds=config.general.agent_idle_max_waiting_on_child_seconds,
                idle_poll_interval_seconds=config.general.agent_idle_poll_interval_seconds,
                parent_exit_grace_seconds=config.general.agent_parent_exit_grace_seconds,
                descendant_wait_timeout_seconds=config.general.agent_descendant_wait_timeout_seconds,
                descendant_wait_poll_seconds=config.general.agent_descendant_wait_poll_seconds,
                process_exit_wait_seconds=config.general.agent_process_exit_wait_seconds,
                max_session_seconds=config.general.agent_max_session_seconds,
                session_id=resume_session_id,
                system_prompt_file=system_prompt_file,
                phase=str(effect.phase),
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
            _set_last_captured_session_id(extract_session_id(raw_output))
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

    if _failure_requires_fresh_session(exc, inactivity_error_type):
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

    resumable_session_id = cast("object", getattr(exc, "resumable_session_id", None))
    if isinstance(resumable_session_id, str) and resumable_session_id:
        return _AgentRecoveryPlan(
            prompt_file=effect.prompt_file,
            session_id=resumable_session_id,
            reason=reason,
        )

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


def _failure_requires_fresh_session(
    exc: Exception,
    inactivity_error_type: type[Exception],
) -> bool:
    if isinstance(exc, inactivity_error_type):
        session_resume_safe = cast("object", getattr(exc, "session_resume_safe", False))
        return session_resume_safe is not True

    raw_details = "\n".join(_recovery_error_parts(exc))
    from ralph.recovery.classifier import _SESSION_NOT_FOUND_SUBSTRINGS  # noqa: PLC0415

    return any(s in raw_details for s in _SESSION_NOT_FOUND_SUBSTRINGS)


def _retryable_agent_failure_reason(
    exc: Exception,
    inactivity_error_type: type[Exception],
) -> str | None:
    if isinstance(exc, inactivity_error_type):
        return "an inactivity timeout"

    if type(exc).__name__ == "OpenCodeResumableExitError":
        return "OpenCode exited without submitting a required completion artifact"

    raw_details = "\n".join(_recovery_error_parts(exc))
    from ralph.recovery.classifier import _SESSION_NOT_FOUND_SUBSTRINGS  # noqa: PLC0415
    if any(s in raw_details for s in _SESSION_NOT_FOUND_SUBSTRINGS):
        return "a stale session ID (fresh session required)"

    details = raw_details.lower()
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


def _clear_phase_output_artifacts(
    workspace: FsWorkspace,
    phase: str,
    *,
    drain: str | None = None,
) -> None:
    """Remove stale per-phase artifacts before invoking an agent.

    This hardening makes phase handlers reason about outputs created by the
    current invocation instead of silently accepting artifacts left behind by a
    prior interrupted run. Cleanup keys off the active drain when available so
    custom phase names still clear the correct per-drain artifacts.
    """
    for path in _phase_output_artifact_paths(phase, drain=drain):
        workspace.remove(path)


def _phase_output_artifact_paths(phase: str, *, drain: str | None = None) -> tuple[str, ...]:
    artifact_paths_by_drain = {
        "development": (
            DEV_RESULT_ARTIFACT_JSON_PATH,
            ".agent/artifacts/parallel_development_summary.json",
            ".agent/DEVELOPMENT_RESULT.md",
        ),
        "development_analysis": (
            DEV_ANALYSIS_DECISION_JSON_PATH,
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        ),
        "review": (ISSUES_ARTIFACT_JSON_PATH, ".agent/ISSUES.md"),
        "review_analysis": (
            REVIEW_ANALYSIS_DECISION_JSON_PATH,
            ".agent/REVIEW_ANALYSIS_DECISION.md",
        ),
        "fix": (FIX_RESULT_ARTIFACT_JSON_PATH, ".agent/FIX_RESULT.md"),
        "development_commit": (COMMIT_MESSAGE_ARTIFACT,),
        "review_commit": (COMMIT_MESSAGE_ARTIFACT,),
    }
    if drain is not None and drain in artifact_paths_by_drain:
        return artifact_paths_by_drain[drain]
    return artifact_paths_by_drain.get(phase, ())


def _default_mcp_capabilities_for_phase(phase: str) -> set[str]:
    return set(
        build_session_mcp_plan(
            transport=None,
            drain=phase,
            workspace_path=None,
        ).capabilities
    )


def _execute_commit_effect(  # noqa: PLR0913
    effect: CommitEffect,
    create_commit: Callable[[str, str], str],
    stage_all: Callable[[str], None],
    repo_root: Path,
    display: ParallelDisplay | _LegacyConsoleDisplay | None = None,
    *,
    verbosity: Verbosity = Verbosity.VERBOSE,
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
        if verbosity != Verbosity.QUIET and hasattr(display, "emit_phase_close"):
            with suppress(Exception):
                cast("ParallelDisplay", display).emit_phase_close("commit", "commit: prepared")
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
        if parsed_line.type == "thinking" and parsed_line.content.strip():
            line_text = parsed_line.content.strip()
        else:
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

    from ralph.display.activity_router import map_parser_type_to_kind  # noqa: PLC0415
    from ralph.display.parallel_display import ParallelDisplay as _ParallelDisplay  # noqa: PLC0415

    subscriber = _subscriber_for_display(display)
    for parsed_line in parser.parse(_iter_lines()):
        rendered = _render_agent_activity_line(parsed_line, agent_name)
        if rendered is not None and rendered_output_sink is not None:
            rendered_output_sink.append(rendered.plain)
        if isinstance(display, _ParallelDisplay):
            kind = map_parser_type_to_kind(parsed_line.type)
            display.emit_parsed_event(
                agent_name, kind, parsed_line.content, parsed_line.metadata or {}
            )
        elif rendered is not None:
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
        "thinking": lambda: _render_text_line(agent_name, output.content, "dim"),
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
