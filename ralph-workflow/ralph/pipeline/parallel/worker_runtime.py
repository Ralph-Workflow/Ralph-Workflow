"""Early worker-runtime seam for dedicated parallel worker execution.

A manifest-launched worker deliberately does NOT enter the shared run
loop in :mod:`ralph.pipeline.run_loop`, so it does not inherit that
loop's auto-integration seams either. It therefore carries its own:
:func:`run_worker_auto_integration` reproduces the run loop's
``_run_auto_integrate_recovery_preamble`` + ``_run_startup_integration``
preamble before the agent is invoked, and runs again as a boundary
integration after the worker's phase succeeds.

This matters because the manifest-worker topology is precisely the one
auto-integration exists for -- several agents advancing one shared
mainline at the same time. Without these hooks such a worker neither
published its landings to its siblings nor picked theirs up, and the
coordinator-side join that would otherwise have covered it
(``runner._integrate_after_fan_out``, reached via ``FanOutEffect``) is
dormant under the bundled ``dispatch_mode = "agent_subagents"``.

The seam is wrapped so it can never abort a worker: if the policy
bundle, the agent registry, the pipeline dependencies or the display
context are unavailable, the conflict resolvers are simply not built and
the integration runs without the ability to resolve a conflict in place
-- it still performs the cross-agent catch-up, which is the bulk of the
value, and records a conflict rather than resolving one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.config.loader import load_config
from ralph.display.parallel_display import resolve_active_display
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline.auto_integrate import (
    auto_integrate_on_phase_transition,
    recover_incomplete_integration,
)
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
    emit_integration_warn_line,
)
from ralph.pipeline.checkpoint import worker_checkpoint_path
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory, PipelineDeps
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.phase_agent_handler import phase_event_after_agent_run
from ralph.pipeline.prompt_prep import session_capabilities_for_agent_phase
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state_init import create_initial_state
from ralph.policy.loader import load_policy_for_workspace_scope
from ralph.prompts.debug_dump import worker_multimodal_sidecar_path, worker_prompt_dump_path
from ralph.prompts.system_prompt import worker_current_prompt_path, worker_system_prompt_path
from ralph.workspace import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.pro_support.hooks import ProPipelineHooks


@dataclass(frozen=True)
class WorkerRuntimePaths:
    """Worker-local filesystem paths for prompt and checkpoint runtime state."""

    checkpoint_path: Path
    current_prompt_path: Path
    prompt_dump_path: Path
    system_prompt_path: Path
    multimodal_sidecar_path: Path


def build_worker_runtime_paths(
    *,
    workspace_root: Path,
    worker_namespace: Path,
    phase: str,
) -> WorkerRuntimePaths:
    """Return the namespaced runtime paths a parallel worker should own."""

    del workspace_root
    return WorkerRuntimePaths(
        checkpoint_path=worker_checkpoint_path(worker_namespace),
        current_prompt_path=worker_current_prompt_path(worker_namespace),
        prompt_dump_path=worker_prompt_dump_path(worker_namespace, phase),
        system_prompt_path=worker_system_prompt_path(worker_namespace, phase),
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
    """Persist a rendered worker prompt without rewriting identical content."""
    backend.mkdir(prompt_path.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, prompt_path, rendered_prompt, encoding="utf-8")


def _worker_integration_resolvers(
    *,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle | None,
    registry: AgentRegistry | None,
    pipeline_deps: PipelineDeps | None,
    display_context: DisplayContext | None,
) -> tuple[ConflictResolver | None, RebaseStopResolver | None, ParallelDisplay | None]:
    """Build the worker's conflict resolvers, or decline when it cannot.

    Returns ``(conflict_resolver, rebase_stop_resolver, display)``, all
    three ``None`` when any dependency an MCP-backed resolution session
    needs is missing. Declining here rather than passing partially-built
    collaborators matches
    :mod:`ralph.pipeline.auto_integrate_agent`'s own contract: a
    resolver that cannot open a Ralph MCP session must decline, never
    fall back to invoking an agent without one.
    """
    if (
        policy_bundle is None
        or registry is None
        or pipeline_deps is None
        or display_context is None
    ):
        # WARNING, not DEBUG: a worker that integrates with no conflict
        # resolution at all will abort on the first real conflict, and
        # the operator needs that on the transcript rather than buried in
        # a log file. The display is resolved defensively here because
        # this branch is exactly the one where display_context may be the
        # missing dependency.
        logger.warning(
            "auto_integrate: parallel worker has no resolution dependencies; "
            "integrating without in-place conflict resolution"
        )
        if display_context is not None:
            emit_integration_warn_line(
                resolve_active_display(None, display_context),
                "conflict resolution unavailable: parallel worker has no "
                "resolution dependencies",
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
    )
    rebase_stop_resolver = build_agent_rebase_stop_resolver(
        policy_bundle=policy_bundle,
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
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
) -> RebaseState | None:
    """Run one auto-integration seam on behalf of a parallel worker.

    Args:
        config: The worker's own resolved run configuration.
        workspace_scope: The worker's own scope. Its root is what keys
            the boundary refresh throttle, so several workers in one
            process cannot steal each other's refresh window.
        policy_bundle: Resolved policy, or ``None`` to skip resolution.
        registry: Agent registry, or ``None`` to skip resolution.
        pipeline_deps: Pipeline dependencies, or ``None`` to skip
            resolution.
        display_context: Display context, or ``None`` to skip resolution.
        recover_first: Whether to reconcile a durable crash record left
            by an interrupted integration before integrating. True for
            the startup seam, False for the per-phase boundary.

    Returns:
        The recorded :class:`~ralph.pipeline.rebase_state.RebaseState`,
        or ``None`` when the hook decided there was nothing to do.

    Never raises: a failure anywhere in this seam is logged and
    swallowed, because an integration problem must never abort the
    worker whose actual job is the phase it was launched for.
    """
    # Cheap stat guard BEFORE anything else, mirroring the one
    # ``auto_integrate_on_phase_transition`` opens with: a worker whose
    # workspace is not a git checkout has nothing to recover and nothing
    # to integrate, and must not pay for a crash-record read, a display
    # or two resolver closures to find that out.
    if not (Path(workspace_scope.root) / ".git").exists():
        return None
    try:
        if recover_first:
            recovered = recover_incomplete_integration(
                workspace_scope, config=config
            )
            if recovered is not None:
                logger.info(
                    "auto_integrate: parallel worker reconciled an interrupted "
                    "integration: {}",
                    recovered.last_action,
                )
        conflict_resolver, rebase_stop_resolver, display = (
            _worker_integration_resolvers(
                config=config,
                workspace_scope=workspace_scope,
                policy_bundle=policy_bundle,
                registry=registry,
                pipeline_deps=pipeline_deps,
                display_context=display_context,
            )
        )
        outcome = auto_integrate_on_phase_transition(
            config,
            workspace_scope,
            RebaseState(),
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


def run_parallel_worker_from_manifest(
    *,
    manifest_path: Path,
    display_context: DisplayContext,
    pipeline_deps: PipelineDeps | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> int:
    """Execute one manifest-backed worker flow without entering the shared run loop.

    ``model_identity`` and ``pro_hooks`` are forwarded into the shared dependency
    composition path so a parallel worker uses the same injected collaborators as
    the direct run path. They are ignored when an explicit ``pipeline_deps`` bundle
    is supplied.
    """

    manifest = ParallelWorkerManifest.load(manifest_path)
    workspace_root = Path(manifest.workspace_root)
    worker_namespace = Path(manifest.worker_namespace)
    workspace_scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=workspace_root,
        allowed_directories=tuple(manifest.allowed_directories),
        worker_namespace=worker_namespace,
    )
    config = load_config(
        Path(manifest.config_path) if manifest.config_path is not None else None,
        manifest.cli_overrides,
        workspace_scope=workspace_scope,
    )
    policy_bundle = load_policy_for_workspace_scope(workspace_scope, config=config)
    state = _state_for_worker_manifest(manifest, config=config, policy_bundle=policy_bundle)
    effect = determine_effect_from_policy(state, policy_bundle, workspace_scope, config=config)
    if not isinstance(effect, InvokeAgentEffect):
        logger.error(
            "Parallel worker manifest resolved unsupported effect for phase={} effect={}",
            manifest.phase,
            type(effect).__name__,
        )
        return 1

    # The worker bootstrap is trusted orchestrator code and must read shared
    # inputs at the repo root (PROMPT.md, plan artifacts) to materialize the
    # prompt — the agent-facing write restriction is enforced separately via
    # workspace_scope, which execute_agent_effect uses for the MCP surface.
    workspace = FsWorkspace(workspace_root)
    registry = AgentRegistry.from_config(config)
    agent = registry.get(effect.agent_name)
    effective_pipeline_deps = pipeline_deps or DefaultPipelineFactory().build(
        config,
        display_context,
        model_identity=model_identity,
        pro_hooks=pro_hooks,
    )
    # Startup seam: reconcile any interrupted integration and catch up
    # with whatever the sibling agents landed while this worker was being
    # scheduled, BEFORE the prompt is materialized -- otherwise the
    # worker plans and develops against code the fleet has already moved
    # past.
    run_worker_auto_integration(
        config=config,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        registry=registry,
        pipeline_deps=effective_pipeline_deps,
        display_context=display_context,
        recover_first=True,
    )
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
    event: Event = execute_agent_effect(
        worker_effect,
        config,
        effective_pipeline_deps,
        workspace_scope,
        display_context=display_context,
        state=state,
        policy_bundle=policy_bundle,
        worker_namespace=worker_namespace,
        worker_artifact_dir=Path(manifest.worker_artifact_dir),
        parallel_worker=True,
    )
    if event == PipelineEvent.AGENT_SUCCESS:
        event = phase_event_after_agent_run(
            effect=worker_effect,
            config=config,
            policy_bundle=policy_bundle,
            workspace=workspace,
            workspace_scope=workspace_scope,
            display_context=display_context,
            state=state,
        )
    if event == PipelineEvent.AGENT_SUCCESS:
        # Boundary seam: publish whatever this worker just landed to the
        # rest of the fleet, and pick up anything they landed meanwhile.
        # This is the manifest-worker equivalent of the run loop's
        # phase-transition hook, and the only place a worker's work
        # reaches its siblings without waiting for the coordinator.
        run_worker_auto_integration(
            config=config,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=effective_pipeline_deps,
            display_context=display_context,
            recover_first=False,
        )
    return 0 if event == PipelineEvent.AGENT_SUCCESS else 1


__all__ = [
    "WorkerRuntimePaths",
    "build_worker_runtime_paths",
    "run_parallel_worker_from_manifest",
    "run_worker_auto_integration",
]
