"""Prompt materialization helpers for the pipeline runner."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.protocol.capability_mapping import DrainClass
from ralph.mcp.protocol.env import WORKER_NAMESPACE_ENV
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.prompts.materialize import (
    collect_media_entries_for_phase,
    materialize_prompt_for_phase,
    tool_name_prefix_for_transport,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.config.models import AgentConfig
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, ArtifactsPolicy, PipelinePolicy, PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


class _RegistryLike(Protocol):
    def get(self, name: str) -> AgentConfig | None: ...


def _prompt_session_drain_for_phase(
    drain: str | None,
    *,
    agents_policy: AgentsPolicy | None = None,
) -> SessionDrain:
    """Return the prompt capability profile for a policy drain."""
    if drain is not None:
        try:
            return SessionDrain(drain)
        except ValueError:
            if agents_policy is not None:
                drain_cfg = agents_policy.agent_drains.get(drain)
                if drain_cfg is not None:
                    drain_class = drain_cfg.capability_class or drain_cfg.drain_class
                    if drain_class is not None:
                        return {
                            DrainClass.PLANNING: SessionDrain.PLANNING,
                            DrainClass.DEVELOPMENT: SessionDrain.DEVELOPMENT,
                            DrainClass.ANALYSIS: SessionDrain.ANALYSIS,
                            DrainClass.REVIEW: SessionDrain.REVIEW,
                            DrainClass.FIX: SessionDrain.FIX,
                            DrainClass.COMMIT: SessionDrain.COMMIT,
                        }[DrainClass(drain_class)]
            raise
    return SessionDrain("cli")


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
    materialize_fn: Callable[..., None] | None = None,
) -> None:
    env_map = os.environ if env is None else env
    workspace = FsWorkspace(
        workspace_scope.root,
        allowed_roots=workspace_scope.allowed_roots,
    )
    worker_ns_str = env_map.get(WORKER_NAMESPACE_ENV)
    worker_namespace = Path(worker_ns_str) if worker_ns_str else None
    media_entries = collect_media_entries_for_phase(workspace, effect.phase) or None
    _mat = materialize_fn or materialize_prompt_for_phase
    _mat(
        phase=effect.phase,
        workspace=workspace,
        pipeline_policy=pipeline_policy,
        session_caps=SessionCapabilities.defaults_for_drain(
            _prompt_session_drain_for_phase(
                effect.drain
                or resolve_phase_drain(effect.phase, pipeline_policy)
                or effect.phase,
                agents_policy=agents_policy,
            )
        ),
        workspace_root=workspace_scope.root,
        artifacts_policy=artifacts_policy,
        worker_namespace=worker_namespace,
        previous_phase=effect.previous_phase,
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
    materialize_fn: Callable[..., None] | None = None,
) -> None:
    if not isinstance(effect, InvokeAgentEffect):
        return

    agent = registry.get(effect.agent_name)
    tool_name_prefix = ""
    if agent is not None:
        tool_name_prefix = tool_name_prefix_for_transport(agent.transport)

    media_entries = collect_media_entries_for_phase(workspace, effect.phase) or None
    _mat = materialize_fn or materialize_prompt_for_phase
    _mat(
        phase=effect.phase,
        workspace=workspace,
        pipeline_policy=policy_bundle.pipeline,
        session_caps=SessionCapabilities.defaults_for_drain(
            _prompt_session_drain_for_phase(
                effect.drain
                or resolve_phase_drain(effect.phase, policy_bundle.pipeline)
                or effect.phase,
                agents_policy=policy_bundle.agents,
            ),
            tool_name_prefix=tool_name_prefix,
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
