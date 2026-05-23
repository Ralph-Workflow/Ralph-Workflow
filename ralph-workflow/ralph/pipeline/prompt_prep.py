"""Prompt materialization helpers for the pipeline runner."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.capability_mapping import DrainClass, SessionDrain
from ralph.mcp.protocol.env import WORKER_NAMESPACE_ENV
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.effect_router import agents_for_phase
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.prompts.materialize import (
    collect_media_entries_for_phase,
    materialize_prompt_for_phase,
    tool_name_prefix_for_transport,
)
from ralph.prompts.types import SessionCapabilities
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Protocol

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, ArtifactsPolicy, PipelinePolicy, PolicyBundle
    from ralph.prompts.materialize import PromptPhaseContext, PromptPhaseOptions
    from ralph.workspace.scope import WorkspaceScope

    class _RegistryLike(Protocol):
        def get(self, name: str) -> AgentConfig | None: ...

    class _MaterializePromptFn(Protocol):
        def __call__(
            self,
            context: PromptPhaseContext | None = ...,
            options: PromptPhaseOptions | None = ...,
            **kwargs: object,
        ) -> str: ...


def _session_drain_for_drain_class(drain_class: DrainClass) -> SessionDrain:
    return {
        DrainClass.PLANNING: SessionDrain.PLANNING,
        DrainClass.DEVELOPMENT: SessionDrain.DEVELOPMENT,
        DrainClass.ANALYSIS: SessionDrain.ANALYSIS,
        DrainClass.REVIEW: SessionDrain.REVIEW,
        DrainClass.FIX: SessionDrain.FIX,
        DrainClass.COMMIT: SessionDrain.COMMIT,
    }[drain_class]


def _fallback_prompt_session_drain_for_role(role: str | None) -> SessionDrain:
    if role == "execution":
        return SessionDrain.DEVELOPMENT
    if role == "analysis":
        return SessionDrain.ANALYSIS
    if role == "review":
        return SessionDrain.REVIEW
    if role == "commit":
        return SessionDrain.COMMIT
    if role == "fix":
        return SessionDrain.FIX
    return SessionDrain.ANALYSIS


def _prompt_session_drain_for_phase(
    drain: str | None,
    *,
    phase: str | None = None,
    pipeline_policy: PipelinePolicy | None = None,
    agents_policy: AgentsPolicy | None = None,
) -> SessionDrain:
    """Return the prompt capability profile for a policy drain."""
    candidate_drains: list[str] = []
    if drain is not None:
        candidate_drains.append(drain)

    phase_def = None
    if phase is not None and pipeline_policy is not None and hasattr(pipeline_policy, "phases"):
        phase_def = pipeline_policy.phases.get(phase)
        phase_drain = phase_def.drain if phase_def is not None else None
        if phase_drain is not None and phase_drain not in candidate_drains:
            candidate_drains.append(phase_drain)

    for candidate in candidate_drains:
        try:
            return SessionDrain(candidate)
        except ValueError:
            if agents_policy is not None:
                drain_cfg = agents_policy.agent_drains.get(candidate)
                if drain_cfg is not None:
                    drain_class = drain_cfg.capability_class or drain_cfg.drain_class
                    if drain_class is not None:
                        return _session_drain_for_drain_class(DrainClass(drain_class))

    if phase_def is not None:
        return _fallback_prompt_session_drain_for_role(phase_def.role)

    return SessionDrain("cli")


def session_capabilities_for_agent_phase(
    drain: str | None,
    *,
    phase: str | None = None,
    pipeline_policy: PipelinePolicy | None = None,
    agent: AgentConfig | None = None,
    agents_policy: AgentsPolicy | None = None,
) -> SessionCapabilities:
    """Return prompt session capabilities with the effective transport tool prefix."""
    tool_name_prefix = ""
    if agent is not None:
        tool_name_prefix = tool_name_prefix_for_transport(agent.transport)
    return SessionCapabilities.defaults_for_drain(
        _prompt_session_drain_for_phase(
            drain,
            phase=phase,
            pipeline_policy=pipeline_policy,
            agents_policy=agents_policy,
        ),
        tool_name_prefix=tool_name_prefix,
    )


def _prompt_changed_since_last_materialization(workspace_root: Path) -> bool:
    prompt_path = workspace_root / "PROMPT.md"
    current_prompt_path = workspace_root / ".agent" / "CURRENT_PROMPT.md"
    if not prompt_path.exists() or not current_prompt_path.exists():
        return False
    try:
        return prompt_path.read_text(encoding="utf-8") != current_prompt_path.read_text(
            encoding="utf-8"
        )
    except OSError:
        return False


def _should_resume_existing_planning_phase_name(
    *,
    phase: str,
    drain: str | None,
    state: PipelineState | None,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
) -> bool:
    if state is None or state.phase != phase:
        return False
    if state.previous_phase is not None or state.checkpoint_saved_count <= 0:
        return False
    effective_drain = drain or resolve_phase_drain(phase, pipeline_policy) or phase
    required_artifact = resolve_phase_required_artifact(
        pipeline_policy,
        artifacts_policy,
        phase=phase,
        drain=effective_drain,
    )
    return bool(required_artifact is not None and required_artifact.artifact_type == "plan")


def _should_resume_existing_planning_phase(
    *,
    effect: InvokeAgentEffect,
    state: PipelineState,
    policy_bundle: PolicyBundle,
) -> bool:
    return _should_resume_existing_planning_phase_name(
        phase=effect.phase,
        drain=effect.drain,
        state=state,
        pipeline_policy=policy_bundle.pipeline,
        artifacts_policy=policy_bundle.artifacts,
    )


def _materialize_prepared_prompt(
    effect: PreparePromptEffect,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
    workspace_scope: WorkspaceScope,
    agents_policy: AgentsPolicy | None = None,
    state: PipelineState | None = None,
    env: Mapping[str, str] | None = None,
    materialize_fn: _MaterializePromptFn | None = None,
    *,
    registry: _RegistryLike | None = None,
    config: UnifiedConfig | None = None,
) -> None:
    env_map = os.environ if env is None else env
    workspace = FsWorkspace(
        workspace_scope.root,
        allowed_roots=workspace_scope.allowed_roots,
    )
    worker_ns_str = env_map.get(WORKER_NAMESPACE_ENV)
    worker_namespace = Path(worker_ns_str) if worker_ns_str else None
    work_unit = None
    if worker_namespace is not None and state is not None and len(state.work_units) == 1:
        work_unit = state.work_units[0]
    phase_drain = effect.drain or resolve_phase_drain(effect.phase, pipeline_policy) or effect.phase
    agent = None
    if registry is not None and config is not None:
        agent_names = agents_for_phase(
            config,
            effect.phase,
            agents_policy=agents_policy,
            pipeline_policy=pipeline_policy,
        )
        if agent_names:
            agent = registry.get(agent_names[0])
    media_entries = collect_media_entries_for_phase(workspace, effect.phase) or None
    _mat = materialize_fn or materialize_prompt_for_phase
    _mat(
        phase=effect.phase,
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        session_caps=session_capabilities_for_agent_phase(
            phase_drain,
            phase=effect.phase,
            pipeline_policy=pipeline_policy,
            agent=agent,
            agents_policy=agents_policy,
        ),
        workspace_root=workspace_scope.root,
        artifacts_policy=artifacts_policy,
        worker_namespace=worker_namespace,
        previous_phase=effect.previous_phase,
        work_unit=work_unit,
        resume_existing_phase=(
            not _prompt_changed_since_last_materialization(workspace_scope.root)
            and _should_resume_existing_planning_phase_name(
                phase=effect.phase,
                drain=effect.drain,
                state=state,
                pipeline_policy=pipeline_policy,
                artifacts_policy=artifacts_policy,
            )
        ),
        multimodal_entries=media_entries,
    )


def _materialize_agent_prompt_if_needed(
    effect: Effect,
    state: PipelineState,
    workspace: FsWorkspace,
    policy_bundle: PolicyBundle,
    registry: _RegistryLike,
    *,
    materialize_fn: _MaterializePromptFn | None = None,
) -> None:
    if not isinstance(effect, InvokeAgentEffect):
        return

    agent = registry.get(effect.agent_name)
    media_entries = collect_media_entries_for_phase(workspace, effect.phase) or None
    _mat = materialize_fn or materialize_prompt_for_phase
    _mat(
        phase=effect.phase,
        workspace=workspace,
        pipeline_policy=policy_bundle.pipeline,
        session_caps=session_capabilities_for_agent_phase(
            effect.drain
            or resolve_phase_drain(effect.phase, policy_bundle.pipeline)
            or effect.phase,
            phase=effect.phase,
            pipeline_policy=policy_bundle.pipeline,
            agent=agent,
            agents_policy=policy_bundle.agents,
        ),
        workspace_root=workspace.root,
        artifacts_policy=policy_bundle.artifacts,
        previous_phase=state.previous_phase,
        resume_existing_phase=(
            not _prompt_changed_since_last_materialization(workspace.root)
            and _should_resume_existing_planning_phase(
                effect=effect,
                state=state,
                policy_bundle=policy_bundle,
            )
        ),
        multimodal_entries=media_entries,
    )


materialize_prepared_prompt = _materialize_prepared_prompt
materialize_agent_prompt_if_needed = _materialize_agent_prompt_if_needed
prompt_session_drain_for_phase = _prompt_session_drain_for_phase
