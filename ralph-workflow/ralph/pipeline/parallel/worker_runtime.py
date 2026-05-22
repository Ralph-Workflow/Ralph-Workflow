"""Early worker-runtime seam for dedicated parallel worker execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.invoke import AgentInvocationError, invoke_agent
from ralph.agents.registry import AgentRegistry
from ralph.config.loader import load_config
from ralph.pipeline.agent_execution_deps import AgentExecutionDeps
from ralph.pipeline.checkpoint import worker_checkpoint_path
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.phase_agent_handler import phase_event_after_agent_run
from ralph.pipeline.prompt_prep import session_capabilities_for_agent_phase
from ralph.pipeline.state_init import create_initial_state
from ralph.policy.loader import load_policy_for_workspace_scope
from ralph.prompts.debug_dump import worker_multimodal_sidecar_path, worker_prompt_dump_path
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.prompts.system_prompt import worker_current_prompt_path, worker_system_prompt_path
from ralph.workspace import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle


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


def run_parallel_worker_from_manifest(
    *, manifest_path: Path, display_context: DisplayContext
) -> int:
    """Execute one manifest-backed worker flow without entering the shared run loop."""

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

    workspace = FsWorkspace(workspace_scope.root, allowed_roots=workspace_scope.allowed_roots)
    agent = AgentRegistry.from_config(config).get(effect.agent_name)
    prompt_path = materialize_prompt_for_phase(
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
    resolved_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_prompt_path.write_text(rendered_prompt, encoding="utf-8")

    worker_effect = InvokeAgentEffect(
        agent_name=effect.agent_name,
        phase=effect.phase,
        prompt_file=str(resolved_prompt_path),
        drain=effect.drain,
        chain_name=effect.chain_name,
    )
    deps = AgentExecutionDeps(
        invoke_agent=invoke_agent,
        agent_invocation_error=AgentInvocationError,
        agent_registry=AgentRegistry,
    )
    event: Event = execute_agent_effect(
        worker_effect,
        config,
        deps,
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
    return 0 if event == PipelineEvent.AGENT_SUCCESS else 1


def execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    deps: AgentExecutionDeps,
    workspace_scope: WorkspaceScope,
    **opts: object,
) -> PipelineEvent:
    """Delegate worker-mode agent execution through the standard runner seam."""

    from ralph.pipeline.runner import execute_agent_effect as runner_execute_agent_effect

    return runner_execute_agent_effect(effect, config, deps, workspace_scope, **opts)


__all__ = [
    "WorkerRuntimePaths",
    "build_worker_runtime_paths",
    "run_parallel_worker_from_manifest",
]
