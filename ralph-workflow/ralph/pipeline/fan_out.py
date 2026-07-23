"""Fan-out parallel execution for the pipeline runner.

Dormant since the parallelization rework; not invoked by the effect router
when ``dispatch_mode='agent_subagents'`` (the bundled default). Retained for
future use. Re-arm by setting ``[phases.<phase>.parallelization] dispatch_mode
= 'ralph_fan_out'`` on the relevant phase in pipeline.toml.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.executor.process import run_process_async
from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.artifacts.store import list_artifacts
from ralph.mcp.server.factory_impl import DynamicBindingMcpServerFactory
from ralph.mcp.session_plan import SessionMcpPlan, SessionModelOpts, build_session_mcp_plan
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.effect_router import config_agents_for_phase as _config_agents_for_phase
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import (
    PhaseFailureEvent,
    PipelineEvent,
    PostFanoutVerificationEvent,
    WorkerFailedEvent,
)
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.parallel.mode import SameWorkspaceContext
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.parallel.worker_runtime import build_worker_runtime_paths
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.verification_result import VerificationResult
from ralph.pipeline.work_units import (
    WorkUnitsPlan,
    WorkUnitsValidationError,
    validate_for_same_workspace,
)
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.policy.validation import PolicyValidationError
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from rich.console import Console

    from ralph.agents.executor import AgentExecutor
    from ralph.config.enums import AgentTransport
    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.executor.process import ProcessResult
    from ralph.mcp.server.factory import McpServerFactory
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.parallel import coordinator as parallel_coordinator
    from ralph.pipeline.state import PipelineState
    from ralph.pipeline.work_units import WorkUnit
    from ralph.policy.models import PipelinePolicy, PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


if TYPE_CHECKING:

    class _PipelineSubscriberLike(Protocol):
        def notify(self, state: PipelineState) -> None: ...

    class _InstallSignalHandlersFn(Protocol):
        def __call__(self, *args: object, **kwargs: object) -> Callable[[], None] | None: ...

    class _ExecutorFactory(Protocol):
        def __call__(self, *args: object, **kwargs: object) -> AgentExecutor: ...

    class _McpFactory(Protocol):
        def __call__(self, *args: object, **kwargs: object) -> McpServerFactory: ...

    class _RunProcessAsyncFn(Protocol):
        async def __call__(self, *args: object, **kwargs: object) -> ProcessResult: ...

    class _ReducerReduceFn(Protocol):
        def __call__(self, *args: object, **kwargs: object) -> tuple[PipelineState, object]: ...


@dataclass(frozen=True)
class _FanOutCtx:
    effect: FanOutEffect
    state: PipelineState
    display: ParallelDisplay
    policy_bundle: PolicyBundle
    workspace_scope: WorkspaceScope
    repo_root: Path
    pipeline_subscriber: _PipelineSubscriberLike | None
    config: UnifiedConfig | None
    config_path: Path | None
    cli_overrides: dict[str, object] | None
    monitor_stop_cb: Callable[[], None] | None
    install_signal_handlers_fn: _InstallSignalHandlersFn | None = None
    executor_cls: _ExecutorFactory | None = None
    mcp_factory_cls: _McpFactory | None = None
    run_process_async_fn: _RunProcessAsyncFn | None = None
    reducer_reduce_fn: _ReducerReduceFn | None = None
    pipeline_deps: PipelineDeps | None = None
    on_successful_completion: Callable[[PipelineState], PipelineState] | None = None


def _notify_subscriber(subscriber: _PipelineSubscriberLike | None, state: PipelineState) -> None:
    if subscriber is not None:
        subscriber.notify(state)


def _save_checkpoint_or_log(state: PipelineState, *, message: str) -> None:
    try:
        ckpt.save(state)
    except Exception as exc:
        logger.exception(message, phase=state.phase, err=exc)


def write_parallel_development_summary(
    workspace_scope: WorkspaceScope,
    effect: FanOutEffect,
    state: PipelineState,
    verification: VerificationResult | None = None,
) -> None:
    """Write .agent/artifacts/parallel_development_summary.json after fan-out completes."""
    v = verification or VerificationResult(ran=False, passed=None, exit_code=None)
    workers: list[dict[str, object]] = []
    for unit in effect.work_units:
        uid = unit.unit_id
        ws = state.worker_states.get(uid)
        artifact_dir = workspace_scope.root / ".agent" / "workers" / uid / "artifacts"
        artifact_count = len(list_artifacts(artifact_dir)) if artifact_dir.exists() else 0

        if ws is None:
            status = "failed"
            final_message: str | None = "Worker state not recorded"
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

        workers.append(
            {
                "unit_id": uid,
                "status": status,
                "artifact_count": artifact_count,
                "final_message": final_message,
            }
        )

    any_failed = any(w["status"] in ("failed", "cancelled", "blocked") for w in workers)
    all_succeeded = not any_failed and len(workers) > 0

    if v.ran and not v.passed:
        workers.append(
            {
                "unit_id": "__verify__",
                "status": "failed",
                "artifact_count": 0,
                "final_message": "workspace verification failed",
            }
        )
        any_failed = True
        all_succeeded = False

    summary: dict[str, object] = {
        "workers": workers,
        "any_failed": any_failed,
        "all_succeeded": all_succeeded,
        "verification": {
            "ran": v.ran,
            "passed": v.passed,
            "exit_code": v.exit_code,
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

    write_parallel_summary_handoff(workspace_scope.root, summary)


def write_parallel_summary_handoff(
    workspace_root: Path,
    summary: Mapping[str, object],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    """Write the markdown handoff for the internally generated parallel summary.

    The summary is engine-generated (never an agent-submitted markdown
    artifact), so fan-out renders the handoff itself. It reuses the
    development-result handoff path so the analysis phase picks it up through
    the same fallback path without code changes.
    """
    relative_path = handoff_path_for_artifact("parallel_development_summary")
    if relative_path is None:
        return None
    destination = workspace_root / relative_path
    backend.mkdir(destination.parent, parents=True, exist_ok=True)
    write_text_if_changed(
        backend, destination, _render_parallel_summary_markdown(summary), encoding="utf-8"
    )
    return relative_path


def _render_parallel_summary_markdown(content: Mapping[str, object]) -> str:
    """Render the parallel development summary for analysis agent consumption."""
    lines = ["# Parallel Development Summary"]

    workers = content.get("workers")
    if isinstance(workers, list) and workers:
        lines.extend(["", "## Workers"])
        for w in workers:
            if not isinstance(w, dict):
                continue
            uid = w.get("unit_id", "?")
            status = w.get("status", "unknown")
            artifact_count = w.get("artifact_count", 0)
            final_message = w.get("final_message")
            entry = f"- **{uid}**: {status} ({artifact_count} artifact(s))"
            if final_message:
                entry += f" — {final_message}"
            lines.append(entry)

    any_failed = content.get("any_failed", False)
    all_succeeded = content.get("all_succeeded", False)
    lines.extend(
        [
            "",
            "## Status",
            "",
            f"- any_failed: {str(any_failed).lower()}",
            f"- all_succeeded: {str(all_succeeded).lower()}",
        ]
    )

    verification = content.get("verification")
    if isinstance(verification, dict):
        ran = verification.get("ran", False)
        passed = verification.get("passed")
        exit_code = verification.get("exit_code")
        lines.extend(["", "## Verification"])
        if ran:
            result = "passed" if passed else f"failed (exit code {exit_code})"
            lines.extend(["", f"Ran: yes — {result}"])
        else:
            lines.extend(["", "Ran: no"])

    return "\n".join(lines).rstrip() + "\n"


def _parallel_display_cls() -> type[ParallelDisplay]:
    module = import_module("ralph.display.parallel_display")
    return cast("type[ParallelDisplay]", module.ParallelDisplay)


def _fan_out_display_and_subscriber(
    display: ParallelDisplay,
    pipeline_subscriber: _PipelineSubscriberLike | None,
    dashboard_subscriber: _PipelineSubscriberLike | None,
) -> tuple[ParallelDisplay, _PipelineSubscriberLike | None]:
    parallel_display_cls = _parallel_display_cls()
    if isinstance(display, parallel_display_cls):
        parallel_display = display
    else:
        console = cast("Console | None", getattr(display, "console", None))
        parallel_display = parallel_display_cls(make_display_context(console=console))
    effective_subscriber = dashboard_subscriber or pipeline_subscriber
    if effective_subscriber is None and hasattr(parallel_display, "subscriber"):
        effective_subscriber = cast(
            "_PipelineSubscriberLike | None",
            getattr(parallel_display, "subscriber", None),
        )
    return parallel_display, effective_subscriber


def _build_session_mcp_plan_for_phase(
    effect: FanOutEffect,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig | None,
) -> tuple[SessionMcpPlan, str]:
    """Build session MCP plan for fan-out workers matching the serial execution contract."""
    phase_def = policy_bundle.pipeline.phases.get(effect.phase)

    _effect_drain = cast("str | None", getattr(effect, "drain", None))
    drain: str = (
        cast("str", _effect_drain)
        or (phase_def.drain if phase_def and hasattr(phase_def, "drain") else None)
        or effect.phase
        or "development"
    )

    agent_name: str | None = None
    if phase_def is not None:
        config_agents = _config_agents_for_phase(
            config,
            phase=effect.phase,
            policy_drain=drain,
        )
        if config_agents:
            agent_name = config_agents[0]
        else:
            drain_binding = policy_bundle.agents.agent_drains.get(drain)
            if drain_binding is not None:
                chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
                if chain_config is not None and chain_config.agents:
                    agent_name = chain_config.agents[0]

    agent_config = None
    if isinstance(agent_name, str) and agent_name and config is not None:
        registry = AgentRegistry.from_config(config)
        agent_config = registry.get(agent_name)

    _transport_raw = cast("object", getattr(agent_config, "transport", None))
    transport = cast("AgentTransport | None", _transport_raw) if agent_config is not None else None
    _model_flag_raw = cast("object", getattr(agent_config, "model_flag", None))
    model_flag = cast("str | None", _model_flag_raw) if agent_config is not None else None

    effective_agents_policy = (
        policy_bundle.agents
        if policy_bundle is not None
        else load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    )

    try:
        return build_session_mcp_plan(
            transport=transport,
            drain=drain,
            workspace_path=workspace_scope.root,
            agents_policy=effective_agents_policy,
            model_opts=SessionModelOpts(model_flag=model_flag),
        ), drain
    except PolicyValidationError:
        fallback_agents_policy = load_agents_policy_for_workspace_scope(
            workspace_scope, config=config
        )
        return build_session_mcp_plan(
            transport=transport,
            drain=drain,
            workspace_path=workspace_scope.root,
            agents_policy=fallback_agents_policy,
            model_opts=SessionModelOpts(model_flag=model_flag),
        ), drain


def _fan_out_worker_context(
    *,
    workspace_scope: WorkspaceScope,
    repo_root: Path,
    bridge: SignalBridge,
    session_drain: str,
    worker_commands: dict[str, tuple[str, ...]],
    worker_manifest_paths: dict[str, Path],
    session_mcp_plan: SessionMcpPlan,
    executor_cls: _ExecutorFactory | None = None,
    mcp_factory_cls: _McpFactory | None = None,
) -> tuple[AgentExecutor, parallel_coordinator.WorkerContext]:
    _executor_cls = (
        executor_cls
        if executor_cls is not None
        else cast("_ExecutorFactory", SubprocessAgentExecutor)
    )
    _mcp_factory_cls = (
        mcp_factory_cls
        if mcp_factory_cls is not None
        else cast("_McpFactory", DynamicBindingMcpServerFactory)
    )
    executor = _executor_cls(_parallel_worker_command(), signal_bridge=bridge)
    workspace = FsWorkspace(
        workspace_scope.root,
        allowed_roots=workspace_scope.allowed_roots,
    )
    worker_namespace_root = repo_root / ".agent" / "workers"
    worker_namespace_root.mkdir(parents=True, exist_ok=True)
    return executor, coordinator.WorkerContext(
        log=coordinator.WorkerLog(
            log_dir=workspace_scope.root / ".agent" / "logs",
            run_id=str(uuid.uuid4()),
        ),
        same_workspace=SameWorkspaceContext(
            repo_root=repo_root,
            mcp_factory=_mcp_factory_cls(workspace=workspace),
            executor_command=_parallel_worker_command(),
            worker_commands=worker_commands,
            signal_bridge=bridge,
            worker_namespace_root=worker_namespace_root,
            worker_manifest_paths=worker_manifest_paths,
            session_drain=session_drain,
            session_capabilities=session_mcp_plan.capabilities,
            session_model_identity=session_mcp_plan.model_identity,
            session_capability_profile=session_mcp_plan.capability_profile,
        ),
    )


def _persist_parallel_worker_manifests(
    *,
    effect: FanOutEffect,
    repo_root: Path,
    session_drain: str,
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
) -> dict[str, Path]:
    worker_namespace_root = repo_root / ".agent" / "workers"
    manifests: dict[str, Path] = {}
    for unit in effect.work_units:
        worker_namespace = worker_namespace_root / unit.unit_id
        worker_namespace.mkdir(parents=True, exist_ok=True)
        runtime_paths = build_worker_runtime_paths(
            workspace_root=repo_root,
            worker_namespace=worker_namespace,
            phase=effect.phase,
        )
        manifest = ParallelWorkerManifest(
            unit_id=unit.unit_id,
            description=unit.description,
            allowed_directories=list(unit.allowed_directories),
            phase=effect.phase,
            drain=session_drain,
            config_path=str(config_path) if config_path is not None else None,
            cli_overrides=dict(cli_overrides or {}),
            worker_namespace=str(worker_namespace),
            worker_artifact_dir=str(worker_namespace / "artifacts"),
            prompt_file=str(runtime_paths.prompt_dump_path),
            workspace_root=str(repo_root),
        )
        manifest_path = worker_namespace / "worker-manifest.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        manifests[unit.unit_id] = manifest_path
    return manifests


def _worker_commands_from_manifests(
    manifest_paths: dict[str, Path],
) -> dict[str, tuple[str, ...]]:
    return {
        unit_id: _parallel_worker_command(manifest_path)
        for unit_id, manifest_path in manifest_paths.items()
    }


def _cleared_after_successful_wave(state: PipelineState) -> PipelineState:
    """Drop fan-out tracking state once every worker in the wave succeeded.

    A failed or interrupted wave keeps work_units and worker_states so the
    next development entry resumes only the unfinished units.
    """
    worker_states = state.worker_states
    if worker_states and all(ws.status == WorkerStatus.SUCCEEDED for ws in worker_states.values()):
        return state.with_parallel_execution_cleared()
    return state


def _resume_fan_out_state(
    state: PipelineState,
    effect: FanOutEffect,
    pipeline_policy: PipelinePolicy,
    subscriber: _PipelineSubscriberLike | None,
    *,
    reducer_reduce_fn: _ReducerReduceFn | None = None,
) -> tuple[PipelineState, tuple[WorkUnit, ...]]:
    _reduce = (
        reducer_reduce_fn
        if reducer_reduce_fn is not None
        else cast("_ReducerReduceFn", reducer_reduce)
    )
    resumed_state, _ = _reduce(state, PipelineEvent.WORKERS_RESUMED, pipeline_policy)
    _notify_subscriber(subscriber, resumed_state)
    completed_ids = {
        uid
        for uid, ws in resumed_state.worker_states.items()
        if ws.status == WorkerStatus.SUCCEEDED
    }
    resume_units = tuple(u for u in effect.work_units if u.unit_id not in completed_ids)
    return resumed_state, resume_units


async def _run_post_fanout_verification(
    workspace_scope: WorkspaceScope,
    *,
    run_process_async_fn: _RunProcessAsyncFn | None = None,
) -> str | None:
    """Run workspace-wide verification exactly once after all workers complete."""
    logger.debug("Running post-fanout workspace-wide verification (serialized)")
    _run = (
        run_process_async_fn
        if run_process_async_fn is not None
        else cast("_RunProcessAsyncFn", run_process_async)
    )
    verify_result = await _run(
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


async def _run_verify_phase(
    ctx: _FanOutCtx, current: PipelineState, any_worker_failed: bool
) -> tuple[PipelineState, VerificationResult]:
    if not ctx.effect.run_post_fanout_verification:
        return current, VerificationResult(ran=False, passed=None, exit_code=None)
    if any_worker_failed:
        logger.debug("Post-fanout verification skipped: one or more workers failed in this wave")
        return current, VerificationResult(ran=False, passed=None, exit_code=None)
    verify_error = await _run_post_fanout_verification(
        ctx.workspace_scope, run_process_async_fn=ctx.run_process_async_fn
    )
    if verify_error is not None:
        logger.error(verify_error)
        v = VerificationResult(ran=True, passed=False, exit_code=1)
        verify_ev = PostFanoutVerificationEvent(success=False, exit_code=1, error=verify_error)
    else:
        v = VerificationResult(ran=True, passed=True, exit_code=0)
        verify_ev = PostFanoutVerificationEvent(success=True, exit_code=0)
    _reduce = (
        ctx.reducer_reduce_fn
        if ctx.reducer_reduce_fn is not None
        else cast("_ReducerReduceFn", reducer_reduce)
    )
    current, _ = _reduce(current, verify_ev, ctx.policy_bundle.pipeline)
    _notify_subscriber(ctx.pipeline_subscriber, current)
    _save_checkpoint_or_log(
        current,
        message="Checkpoint save failed after verification in phase={phase}: {err}",
    )
    return current, v


async def _run_fan_out_async(ctx: _FanOutCtx) -> PipelineState:
    current = ctx.state
    _reduce = (
        ctx.reducer_reduce_fn
        if ctx.reducer_reduce_fn is not None
        else cast("_ReducerReduceFn", reducer_reduce)
    )
    _install = (
        ctx.install_signal_handlers_fn
        if ctx.install_signal_handlers_fn is not None
        else cast("_InstallSignalHandlersFn", install_signal_handlers)
    )
    teardown_fn: Callable[[], None] | None = None
    try:
        loop = asyncio.get_running_loop()
        bridge = SignalBridge()
        if ctx.monitor_stop_cb is not None:
            bridge._connectivity_stop = ctx.monitor_stop_cb
        root_task = cast("asyncio.Task[object] | None", asyncio.current_task())
        assert root_task is not None
        teardown_fn = _install(loop, root_task, bridge)

        try:
            validate_for_same_workspace(WorkUnitsPlan(work_units=list(ctx.effect.work_units)))
        except WorkUnitsValidationError as exc:
            failure_reason = f"Parallel plan rejected (same-workspace safety check failed): {exc}"
            logger.error(failure_reason)
            failure_event = PhaseFailureEvent(
                phase=current.phase, reason=failure_reason, recoverable=True
            )
            recovered, _ = _reduce(
                current, failure_event, ctx.policy_bundle.pipeline, recovery=None
            )
            _notify_subscriber(ctx.pipeline_subscriber, recovered)
            _save_checkpoint_or_log(
                recovered,
                message="Checkpoint save failed after plan rejection in phase={phase}: {err}",
            )
            return recovered

        session_mcp_plan, session_drain = _build_session_mcp_plan_for_phase(
            effect=ctx.effect,
            policy_bundle=ctx.policy_bundle,
            workspace_scope=ctx.workspace_scope,
            config=ctx.config,
        )
        worker_manifest_paths = _persist_parallel_worker_manifests(
            effect=ctx.effect,
            repo_root=ctx.repo_root,
            session_drain=session_drain,
            config_path=ctx.config_path,
            cli_overrides=ctx.cli_overrides,
        )
        worker_commands = _worker_commands_from_manifests(worker_manifest_paths)
        executor, worker_ctx = _fan_out_worker_context(
            workspace_scope=ctx.workspace_scope,
            repo_root=ctx.repo_root,
            bridge=bridge,
            session_drain=session_drain,
            worker_commands=worker_commands,
            worker_manifest_paths=worker_manifest_paths,
            session_mcp_plan=session_mcp_plan,
            executor_cls=ctx.executor_cls,
            mcp_factory_cls=ctx.mcp_factory_cls,
        )
        # The router derives work units from the plan artifact, so the state
        # entering fan-out may carry empty work_units. The reducer seeds
        # worker_states from state.work_units on FAN_OUT_STARTED, so the wave
        # state must carry the effect's units before any event is reduced.
        seeded_state = (
            ctx.state
            if ctx.state.work_units
            else ctx.state.copy_with(work_units=ctx.effect.work_units)
        )
        current, resume_units = _resume_fan_out_state(
            seeded_state,
            ctx.effect,
            ctx.policy_bundle.pipeline,
            ctx.pipeline_subscriber,
            reducer_reduce_fn=_reduce,
        )
        if not resume_units:
            current, _ = _reduce(
                current, PipelineEvent.ALL_WORKERS_COMPLETE, ctx.policy_bundle.pipeline
            )
            current = _cleared_after_successful_wave(current)
            _notify_subscriber(ctx.pipeline_subscriber, current)
            _save_checkpoint_or_log(
                current,
                message="Checkpoint save failed after resumed fan-out in phase={phase}: {err}",
            )
            return current

        fan_out_events = await coordinator.run_fan_out(
            effect=FanOutEffect(
                work_units=resume_units,
                max_workers=ctx.effect.max_workers,
                phase=ctx.effect.phase,
            ),
            executor=executor,
            display=ctx.display,
            ctx=worker_ctx,
        )
        for ev in fan_out_events:
            current, _ = _reduce(current, ev, ctx.policy_bundle.pipeline)
            _notify_subscriber(ctx.pipeline_subscriber, current)
        # Clear tracking BEFORE checkpointing: a checkpoint that retains
        # work_units past a fully successful wave would hard-fail routing of
        # the advanced (non-parallelized) phase on crash-resume. The pre-clear
        # state is kept solely for the per-worker summary below.
        wave_state = current
        current = _cleared_after_successful_wave(current)
        _save_checkpoint_or_log(
            current,
            message="Checkpoint save failed after fan-out in phase={phase}: {err}",
        )

        any_worker_failed = any(isinstance(ev, WorkerFailedEvent) for ev in fan_out_events)
        current, verification = await _run_verify_phase(ctx, current, any_worker_failed)
        write_parallel_development_summary(
            ctx.workspace_scope, ctx.effect, wave_state, verification
        )
        all_workers_succeeded = (
            len(wave_state.worker_states) == len(ctx.effect.work_units)
            and bool(wave_state.worker_states)
            and all(
                worker.status == WorkerStatus.SUCCEEDED
                for worker in wave_state.worker_states.values()
            )
        )
        verification_passed = (
            not ctx.effect.run_post_fanout_verification or verification.passed is True
        )
        if all_workers_succeeded and verification_passed and ctx.on_successful_completion is not None:
            return ctx.on_successful_completion(current)
        return current
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        logger.exception(
            "Fan-out execution crashed in phase={phase}: {err}",
            phase=current.phase,
            err=exc,
        )
        failure_event = PhaseFailureEvent(
            phase=current.phase,
            reason=f"Fan-out execution crashed: {type(exc).__name__}: {exc}",
            recoverable=True,
        )
        recovered, _ = _reduce(current, failure_event, ctx.policy_bundle.pipeline, recovery=None)
        _notify_subscriber(ctx.pipeline_subscriber, recovered)
        _save_checkpoint_or_log(
            recovered,
            message=(
                "Checkpoint save failed while recording fan-out recovery in phase={phase}: {err}"
            ),
        )
        return recovered
    finally:
        if teardown_fn is not None:
            try:
                teardown_fn()
            except Exception:
                logger.debug("install_signal_handlers teardown raised")


def execute_fan_out_sync(
    *,
    effect: FanOutEffect,
    state: PipelineState,
    display: ParallelDisplay,
    pipeline_deps: PipelineDeps | None = None,
    **opts: object,
) -> PipelineState:
    """Execute fan-out development synchronously by wrapping asyncio.run()."""
    policy_bundle = cast("PolicyBundle", opts["policy_bundle"])
    workspace_scope = cast("WorkspaceScope", opts["workspace_scope"])
    pipeline_subscriber = cast("_PipelineSubscriberLike | None", opts.get("pipeline_subscriber"))
    dashboard_subscriber = cast("_PipelineSubscriberLike | None", opts.get("dashboard_subscriber"))
    config = cast("UnifiedConfig | None", opts.get("config"))
    config_path = cast("Path | None", opts.get("config_path"))
    cli_overrides = cast("dict[str, object] | None", opts.get("cli_overrides"))
    monitor_stop_cb = cast("Callable[[], None] | None", opts.get("_monitor_stop_cb"))
    install_fn = cast("_InstallSignalHandlersFn | None", opts.get("_install_signal_handlers"))
    executor_cls = cast("_ExecutorFactory | None", opts.get("_executor_cls"))
    mcp_factory_cls = cast("_McpFactory | None", opts.get("_mcp_factory_cls"))
    run_process_fn = cast("_RunProcessAsyncFn | None", opts.get("_run_process_async"))
    reducer_fn = cast("_ReducerReduceFn | None", opts.get("_reducer_reduce"))
    on_successful_completion = cast(
        "Callable[[PipelineState], PipelineState] | None", opts.get("_on_successful_completion")
    )

    parallel_display, effective_subscriber = _fan_out_display_and_subscriber(
        display, pipeline_subscriber, dashboard_subscriber
    )
    ctx = _FanOutCtx(
        effect=effect,
        state=state,
        display=parallel_display,
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        repo_root=workspace_scope.root,
        pipeline_subscriber=effective_subscriber,
        config=config,
        config_path=config_path,
        cli_overrides=cli_overrides,
        monitor_stop_cb=monitor_stop_cb,
        install_signal_handlers_fn=install_fn,
        executor_cls=executor_cls,
        mcp_factory_cls=mcp_factory_cls,
        run_process_async_fn=run_process_fn,
        reducer_reduce_fn=reducer_fn,
        pipeline_deps=pipeline_deps,
        on_successful_completion=on_successful_completion,
    )
    return asyncio.run(_run_fan_out_async(ctx))


def _parallel_worker_command(manifest_path: Path | None = None) -> tuple[str, ...]:
    command: tuple[str, ...] = (sys.executable, "-m", "ralph")
    if manifest_path is None:
        return command
    return (*command, "--parallel-worker-manifest", str(manifest_path))
