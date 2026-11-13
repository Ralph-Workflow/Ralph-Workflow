from __future__ import annotations

import uuid
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.agents.system_clock import SystemClock
from ralph.config.enums import Verbosity
from ralph.config.loader import load_config
from ralph.display.parallel_display import resolve_active_display
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.phases.execution import handle_execution_phase
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    register_entry_point,
    registered_entry_points,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.auto_integrate import (
    auto_integrate_on_phase_transition,
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
    emit_integration_warn_line,
)
from ralph.pipeline.auto_integrate_recovery import legacy_rebase_startup_block
from ralph.pipeline.auto_integrate_resolution_state import reconcile_stale_unresolved_state
from ralph.pipeline.checkpoint import worker_checkpoint_path
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import ExecutionResultEvent, PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory, PipelineDeps
from ralph.pipeline.integration_resolution import (
    assert_non_resolution_dispatch_allowed,
    inspect_integration_resolution,
)
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.phase_agent_handler import (
    phase_event_after_agent_run,
    render_phase_failure_report,
)
from ralph.pipeline.prompt_prep import (
    cycle_deadline_suspended,
    session_capabilities_for_agent_phase,
)
from ralph.pipeline.rebase_state import (
    HISTORY_AWARE_CONFLICT_STRATEGY_INDEX,
    RebaseState,
)
from ralph.pipeline.state_init import create_initial_state
from ralph.policy.loader import load_policy_for_workspace_scope
from ralph.prompts.debug_dump import worker_multimodal_sidecar_path, worker_prompt_dump_path
from ralph.prompts.master_prompt import worker_master_prompt_path, worker_product_criteria_path
from ralph.workspace import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.pro_support.hooks import ProPipelineHooks
    from ralph.recovery.controller import RecoveryController

execute_agent_effect = PRODUCTION_INVOKE


@dataclass(frozen=True)
class WorkerRuntimePaths:
    checkpoint_path: Path
    product_criteria_path: Path
    prompt_dump_path: Path
    master_prompt_path: Path
    multimodal_sidecar_path: Path


def build_worker_runtime_paths(
    *,
    workspace_root: Path,
    worker_namespace: Path,
    phase: str,
) -> WorkerRuntimePaths:

    del workspace_root
    return WorkerRuntimePaths(
        checkpoint_path=worker_checkpoint_path(worker_namespace),
        product_criteria_path=worker_product_criteria_path(worker_namespace),
        prompt_dump_path=worker_prompt_dump_path(worker_namespace, phase),
        master_prompt_path=worker_master_prompt_path(worker_namespace, phase),
        multimodal_sidecar_path=worker_multimodal_sidecar_path(worker_namespace, phase),
    )


def _state_for_worker_manifest(
    manifest: ParallelWorkerManifest,
    *,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
) -> PipelineState:
    initial_state = create_initial_state(
        config,
        agents_policy=policy_bundle.agents,
        pipeline_policy=policy_bundle.pipeline,
    )
    unit = manifest.to_work_unit()
    return initial_state.copy_with(
        phase=manifest.phase,
        current_drain=manifest.drain,
        work_units=(unit,),
    )


def _write_worker_prompt(
    prompt_path: Path,
    rendered_prompt: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    write_text_if_changed(
        backend,
        prompt_path,
        rendered_prompt,
        encoding="utf-8",
        prepare_write=lambda: backend.mkdir(prompt_path.parent, parents=True, exist_ok=True),
    )


def _emit_deferred_integration_line(
    display_context: DisplayContext | None, recovered: RebaseState
) -> None:
    if display_context is None:
        return
    with suppress(Exception):
        emit_integration_warn_line(
            resolve_active_display(None, display_context),
            "catch-up integration deferred: crash recovery still owns the"
            f" durable integration record ({recovered.last_reason})",
        )


def _worker_integration_resolvers(
    *,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle | None,
    registry: AgentRegistry | None,
    pipeline_deps: PipelineDeps | None,
    display_context: DisplayContext | None,
    strategy_history: tuple[str, ...] = (),
) -> tuple[ConflictResolver | None, RebaseStopResolver | None, ParallelDisplay | None]:
    if (
        policy_bundle is None
        or registry is None
        or pipeline_deps is None
        or display_context is None
    ):
        logger.warning(
            "auto_integrate: parallel worker has no resolution dependencies; "
            "integrating without in-place conflict resolution"
        )
        if display_context is not None:
            emit_integration_warn_line(
                resolve_active_display(None, display_context),
                "conflict resolution unavailable: parallel worker has no resolution dependencies",
            )
        return None, None, None
    display = resolve_active_display(None, display_context)
    conflict_resolver = build_agent_conflict_resolver(
        policy_bundle=policy_bundle,
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
        strategy_history=strategy_history,
    )
    rebase_stop_resolver = build_agent_rebase_stop_resolver(
        policy_bundle=policy_bundle,
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
        strategy_history=strategy_history,
    )
    return conflict_resolver, rebase_stop_resolver, display


def run_worker_auto_integration(
    *,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle | None,
    registry: AgentRegistry | None,
    pipeline_deps: PipelineDeps | None,
    display_context: DisplayContext | None,
    recover_first: bool,
    state: RebaseState | None = None,
) -> RebaseState | None:
    if not (Path(workspace_scope.root) / ".git").exists():
        return None
    try:
        conflict_resolver, rebase_stop_resolver, display = _worker_integration_resolvers(
            config=config,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=pipeline_deps,
            display_context=display_context,
            strategy_history=(
                state.conflict_strategies_tried
                if state is not None
                and state.conflict_strategy_index == HISTORY_AWARE_CONFLICT_STRATEGY_INDEX
                else ()
            ),
        )
        unresolved = (
            state is not None
            and not inspect_integration_resolution(
                Path(workspace_scope.root),
                state,
            ).dispatch_allowed
        )
        if recover_first or unresolved:
            recovered = recover_incomplete_integration(
                workspace_scope,
                config=config,
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=rebase_stop_resolver,
            )
            if recovered is not None:
                logger.info(
                    "auto_integrate: parallel worker reconciled an interrupted integration: {}",
                    recovered.last_action,
                )
                if recovery_retained_record(recovered):
                    _emit_deferred_integration_line(display_context, recovered)
                    return recovered
                if recovered.fast_forwarded:
                    return recovered
                state = recovered
            if (
                state is not None
                and not inspect_integration_resolution(
                    Path(workspace_scope.root),
                    state,
                ).dispatch_allowed
            ):
                return state
        outcome = auto_integrate_on_phase_transition(
            config,
            workspace_scope,
            state or RebaseState(),
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
        )
    except Exception as integrate_exc:
        logger.warning(
            "auto_integrate: parallel worker integration seam failed: {}",
            integrate_exc,
        )
        return None
    if outcome is not None:
        logger.info(
            "auto_integrate: parallel worker {} target='{}' reason='{}'",
            outcome.last_action,
            outcome.last_target,
            outcome.last_reason,
        )
    return outcome


def _invoke_worker_effect(
    target: InvokeAgentEffect | AgentConfig,
    *args: object,
    **kwargs: object,
) -> object:
    config = kwargs.pop("config")
    pipeline_deps = kwargs.pop("pipeline_deps")
    workspace_scope = kwargs.pop("workspace_scope")
    result: object = execute_agent_effect(
        target,
        config,
        pipeline_deps,
        workspace_scope,
        *args,
        **kwargs,
    )
    return result


def _parallel_worker_controller(
    *,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle | None,
    deps: AttemptRunnerDeps,
    state: PipelineState | None,
    pipeline_deps: PipelineDeps | None,
) -> RecoveryController:
    if isinstance(pipeline_deps, PipelineDeps):
        existing = pipeline_deps.recovery_controller
        if existing is not None:
            return existing
    return build_entry_point_recovery_controller(
        AgentEntryPoint.PARALLEL_WORKER,
        config=config,
        policy_bundle=policy_bundle,
        deps=deps,
        ledger_snapshot=state.ledger_snapshot if state is not None else None,
        false_bench_snapshot=state.false_bench_feedback if state is not None else None,
    )


def _run_parallel_worker_attempt(
    *_args: object,
    effect: object | None = None,
    invocation: object | None = None,
    config: UnifiedConfig,
    controller: RecoveryController,
    deps: AttemptRunnerDeps,
    state: object | None = None,
    **execute_kwargs: object,
) -> AttemptResult:
    return run_agent_attempt(
        AgentEntryPoint.PARALLEL_WORKER,
        effect=effect,
        invocation=invocation,
        config=config,
        controller=controller,
        deps=deps,
        state=state,
        **execute_kwargs,
    )


if AgentEntryPoint.PARALLEL_WORKER not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.PARALLEL_WORKER,
        _run_parallel_worker_attempt,
    )


def run_parallel_worker_from_manifest(
    *,
    manifest_path: Path,
    display_context: DisplayContext,
    pipeline_deps: PipelineDeps | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> int:

    manifest = ParallelWorkerManifest.load(manifest_path)
    workspace_root = Path(manifest.workspace_root)
    worker_namespace = Path(manifest.worker_namespace)
    workspace_scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=workspace_root,
        allowed_directories=tuple(manifest.allowed_directories),
        paths=tuple(manifest.paths),
        worker_namespace=worker_namespace,
    )
    config = load_config(
        Path(manifest.config_path) if manifest.config_path is not None else None,
        manifest.cli_overrides,
        workspace_scope=workspace_scope,
    )
    policy_bundle = load_policy_for_workspace_scope(workspace_scope, config=config)
    state = _state_for_worker_manifest(manifest, config=config, policy_bundle=policy_bundle)
    effective_pipeline_deps = pipeline_deps or DefaultPipelineFactory().build(
        config,
        display_context,
        model_identity=model_identity,
        pro_hooks=pro_hooks,
    )
    effect = determine_effect_from_policy(
        state,
        policy_bundle,
        workspace_scope,
        config=config,
    )
    if not isinstance(effect, InvokeAgentEffect):
        logger.error(
            "Parallel worker manifest resolved unsupported effect for phase={} effect={}",
            manifest.phase,
            type(effect).__name__,
        )
        return 1

    workspace = FsWorkspace(workspace_root)
    legacy_block = legacy_rebase_startup_block(workspace_root)
    if legacy_block is not None:
        logger.warning("auto_integrate: legacy worker startup block: {}", legacy_block.last_reason)
        return 1
    registry = AgentRegistry.from_config(config)
    agent = registry.get(effect.agent_name)
    with cycle_deadline_suspended():
        startup_outcome = run_worker_auto_integration(
            config=config,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=effective_pipeline_deps,
            display_context=display_context,
            recover_first=True,
            state=state.rebase,
        )
    if startup_outcome is not None:
        state = state.copy_with(rebase=startup_outcome)
    verdict = inspect_integration_resolution(workspace_root, state.rebase)
    if verdict.dispatch_allowed:
        state = state.copy_with(rebase=reconcile_stale_unresolved_state(state.rebase))
    try:
        assert_non_resolution_dispatch_allowed(effect.phase, verdict)
    except RuntimeError as exc:
        logger.warning("auto_integrate: worker integration blocks phase invocation: {}", exc)
        return 1
    prompt_path = effective_pipeline_deps.phase_prompt_materializer(
        phase=manifest.phase,
        workspace=workspace,
        pipeline_policy=policy_bundle.pipeline,
        session_caps=session_capabilities_for_agent_phase(
            manifest.drain,
            agent=agent,
            agents_policy=policy_bundle.agents,
        ),
        workspace_root=workspace_root,
        artifacts_policy=policy_bundle.artifacts,
        worker_namespace=worker_namespace,
        work_unit=manifest.to_work_unit(),
    )
    rendered_prompt = workspace.read(prompt_path)
    resolved_prompt_path = Path(manifest.prompt_file)
    _write_worker_prompt(resolved_prompt_path, rendered_prompt, backend=backend)

    worker_effect = InvokeAgentEffect(
        agent_name=effect.agent_name,
        phase=effect.phase,
        prompt_file=str(resolved_prompt_path),
        drain=effect.drain,
        chain_name=effect.chain_name,
    )
    run_id = str(uuid.uuid4())
    attempt_deps = AttemptRunnerDeps(
        clock=effective_pipeline_deps.recovery_clock or SystemClock(),
        invoke=_invoke_worker_effect,
        probe_runner=None,
        workspace_probe=None,
        connectivity=None,
    )
    attempt = _run_parallel_worker_attempt(
        effect=worker_effect,
        config=config,
        controller=_parallel_worker_controller(
            config=config,
            policy_bundle=policy_bundle,
            deps=attempt_deps,
            state=state,
            pipeline_deps=effective_pipeline_deps,
        ),
        deps=attempt_deps,
        state=state,
        pipeline_deps=effective_pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
        policy_bundle=policy_bundle,
        worker_namespace=worker_namespace,
        worker_artifact_dir=Path(manifest.worker_artifact_dir),
        parallel_worker=True,
        run_id=run_id,
    )
    event: object = attempt.event
    if event == PipelineEvent.AGENT_SUCCESS:
        worker_artifact_path = Path(manifest.worker_artifact_dir) / "development_result.md"
        event = phase_event_after_agent_run(
            effect=worker_effect,
            config=config,
            policy_bundle=policy_bundle,
            workspace=workspace,
            workspace_scope=workspace_scope,
            display_context=display_context,
            state=state,
            handle_phase_fn=partial(
                handle_execution_phase,
                output_artifact_path=str(worker_artifact_path),
                assigned_work_unit_id=manifest.unit_id,
            ),
            run_id=run_id,
            shared_capture=True,
        )
    else:
        render_phase_failure_report(
            worker_effect,
            policy_bundle=policy_bundle,
            workspace=workspace,
            display=None,
            display_context=display_context,
            verbosity=Verbosity.VERBOSE,
            run_id=run_id,
            config=config,
            shared_capture=True,
        )
    if isinstance(event, ExecutionResultEvent) and event.status == "completed":
        event = PipelineEvent.AGENT_SUCCESS
    if event == PipelineEvent.AGENT_SUCCESS:
        with cycle_deadline_suspended():
            boundary_outcome = run_worker_auto_integration(
                config=config,
                workspace_scope=workspace_scope,
                policy_bundle=policy_bundle,
                registry=registry,
                pipeline_deps=effective_pipeline_deps,
                display_context=display_context,
                recover_first=False,
                state=startup_outcome,
            )
        boundary_rebase = boundary_outcome or state.rebase
        boundary_verdict = inspect_integration_resolution(workspace_root, boundary_rebase)
        if boundary_verdict.dispatch_allowed:
            boundary_rebase = reconcile_stale_unresolved_state(boundary_rebase)
        if not boundary_verdict.dispatch_allowed:
            logger.warning(
                "auto_integrate: worker boundary blocks successful completion: {}",
                "; ".join(boundary_verdict.reasons),
            )
            return 1
    return 0 if event == PipelineEvent.AGENT_SUCCESS else 1


__all__ = [
    "WorkerRuntimePaths",
    "build_worker_runtime_paths",
    "run_parallel_worker_from_manifest",
    "run_worker_auto_integration",
]
