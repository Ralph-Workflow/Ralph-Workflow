"""Prompt materialization helpers for the pipeline runner."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.protocol.capability_mapping import DrainClass, SessionDrain
from ralph.mcp.protocol.env import (
    CYCLE_DEADLINE_EPOCH_ENV,
    CYCLE_WARN_EPOCH_ENV,
    DEV_DEADLINE_EPOCH_ENV,
    DEV_WARN_EPOCH_ENV,
    WORKER_NAMESPACE_ENV,
)
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
    cycle_deadline_epochs,
    development_deadline_epochs,
)
from ralph.pipeline.effect_router import agents_for_phase
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.pro_support.prompt import resolve_effective_prompt_path
from ralph.prompts.master_prompt import _prompt_files_differ
from ralph.prompts.materialize import (
    collect_media_entries_for_phase,
    materialize_prompt_for_phase,
    tool_name_prefix_for_transport,
)
from ralph.prompts.types import SessionCapabilities
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
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
    """Return True when the operator-visible prompt differs from the last materialised one.

    The operator-visible prompt is resolved through
    :func:`ralph.pro_support.prompt.resolve_effective_prompt_path` so the
    ``PROMPT_PATH`` env var is honoured in Pro mode. The materialised
    ``.agent/PRODUCT_CRITERIA.md`` remains engine-owned and is checked as
    the second operand of the comparison.

    Uses the shared ``_prompt_files_differ`` helper so the comparison
    short-circuits on (st_size, st_mtime_ns) equality without reading
    file content. OSError is still caught and converted to ``False``
    to preserve the pre-existing behaviour.

    When ``product_criteria_path`` does not exist yet (no prior
    materialisation), this returns ``False`` to preserve the legacy
    resume semantics: "no materialised prompt to differ from" is
    treated as "no change", which lets the caller set
    ``resume_existing_phase=True`` on the first invocation.
    """
    prompt_path = resolve_effective_prompt_path(workspace_root, os.environ)
    product_criteria_path = workspace_root / ".agent" / "PRODUCT_CRITERIA.md"

    # Preserve legacy semantics: a missing product_criteria means
    # "no materialisation has happened yet" -> not changed -> the
    # caller treats the planning phase as a resume target.  This is
    # the opposite of what ``_sync_product_criteria_file`` wants
    # (write source on missing current), so the resume caller must
    # short-circuit BEFORE delegating to ``_prompt_files_differ``.
    if not product_criteria_path.exists():
        return False

    def _stat(path: Path) -> tuple[int, int] | None:
        try:
            if not path.exists():
                return None
            stat = path.stat()
            return (stat.st_size, stat.st_mtime_ns)
        except OSError:
            return None

    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    try:
        # read_source is left as None here: this caller only needs the
        # boolean verdict, not the source text, so we avoid reading the
        # source file content on the size-mismatch fast path.
        changed, _ = _prompt_files_differ(
            prompt_path,
            product_criteria_path,
            stat_fn=_stat,
            read_source=None,
            read_current=_read,
        )
    except OSError:
        return False
    return changed


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
    media_entries = (
        collect_media_entries_for_phase(workspace, effect.phase, drain=phase_drain) or None
    )
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


_CYCLE_DEADLINE_ENV_NAMES = (
    CYCLE_WARN_EPOCH_ENV,
    CYCLE_DEADLINE_EPOCH_ENV,
    DEV_WARN_EPOCH_ENV,
    DEV_DEADLINE_EPOCH_ENV,
)


def _withdraw_cycle_deadline_env() -> None:
    """Remove any published cycle deadline from the environment.

    The environment is process-global, so a deadline left published outlives
    the invocation it describes and is inherited by every subprocess spawned
    afterwards — including agents that have nothing to do with the cycle, such
    as the auto-integrate conflict resolver, which would be nagged to wrap up
    work it never started.
    """
    for name in _CYCLE_DEADLINE_ENV_NAMES:
        os.environ.pop(name, None)


@contextmanager
def cycle_deadline_suspended() -> Iterator[None]:
    """Hide the published deadline from agents spawned inside this block.

    Withdrawing outright is wrong where the invocation still needs its own
    deadline afterwards — a fan-out worker rebuilds its prompt warning from
    these values, but reconciles its siblings' work first, and the
    conflict-resolver agent that reconciliation spawns never joined the cycle
    and must not be nagged about it.
    """
    saved = {
        str(name): os.environ[name] for name in _CYCLE_DEADLINE_ENV_NAMES if name in os.environ
    }
    _withdraw_cycle_deadline_env()
    try:
        yield
    finally:
        os.environ.update(saved)


def _publish_cycle_deadline_env(
    state: PipelineState,
    target_phase: str,
    policy_bundle: PolicyBundle,
    cycle_total_elapsed: float | None,
    development_total_elapsed: float | None = None,
) -> None:
    """Publish (or withdraw) the cycle deadline for the invocation about to start.

    The MCP server is a separate process spawned per invocation, so it reads
    the deadline from the environment it inherits rather than from pipeline
    state. Withdrawing is as important as publishing: without it a later
    invocation outside the cycle would inherit a stale deadline and nag the
    agent about a budget that no longer applies.
    """
    routing_timing = (
        RoutingTiming(
            total_elapsed_seconds=cycle_total_elapsed or 0.0,
            development_elapsed_seconds=development_total_elapsed,
        )
        if cycle_total_elapsed is not None or development_total_elapsed is not None
        else None
    )
    cycle_published = cycle_deadline_epochs(
        state,
        target_phase,
        policy=policy_bundle.pipeline,
        routing_timing=routing_timing,
        now_epoch=time.time(),
    )
    development_published = development_deadline_epochs(
        state,
        target_phase,
        policy=policy_bundle.pipeline,
        routing_timing=routing_timing,
        now_epoch=time.time(),
    )
    _withdraw_cycle_deadline_env()
    if cycle_published is not None:
        warn_epoch, deadline_epoch = cycle_published
        os.environ[CYCLE_WARN_EPOCH_ENV] = repr(warn_epoch)
        os.environ[CYCLE_DEADLINE_EPOCH_ENV] = repr(deadline_epoch)
    if development_published is not None:
        warn_epoch, deadline_epoch = development_published
        os.environ[DEV_WARN_EPOCH_ENV] = repr(warn_epoch)
        os.environ[DEV_DEADLINE_EPOCH_ENV] = repr(deadline_epoch)


def _materialize_agent_prompt_if_needed(
    effect: Effect,
    state: PipelineState,
    workspace: FsWorkspace,
    policy_bundle: PolicyBundle,
    registry: _RegistryLike,
    *,
    materialize_fn: _MaterializePromptFn | None = None,
    cycle_total_elapsed: float | None = None,
    development_total_elapsed: float | None = None,
) -> None:
    if not isinstance(effect, InvokeAgentEffect):
        return

    _publish_cycle_deadline_env(
        state,
        effect.phase,
        policy_bundle,
        cycle_total_elapsed,
        development_total_elapsed,
    )

    agent = registry.get(effect.agent_name)
    agent_drain = (
        effect.drain or resolve_phase_drain(effect.phase, policy_bundle.pipeline) or effect.phase
    )
    media_entries = (
        collect_media_entries_for_phase(workspace, effect.phase, drain=agent_drain) or None
    )
    timebox = policy_bundle.pipeline.cycle_timebox
    if (
        timebox is not None
        and cycle_total_elapsed is not None
        and state.cycle_timebox_active
        and effect.phase == timebox.guarded_entry
        and timebox.warning_threshold_seconds <= cycle_total_elapsed < timebox.duration_seconds
    ):
        logger.warning(
            "cycle-timebox warning: {:.0f}s consumed of {:.0f}s budget ({:.0f}% elapsed) on phase {!r}",
            cycle_total_elapsed,
            timebox.duration_seconds,
            cycle_total_elapsed / timebox.duration_seconds * 100.0,
            effect.phase,
        )
    development_timebox = policy_bundle.pipeline.development_timebox
    if (
        development_timebox is not None
        and development_total_elapsed is not None
        and state.dev_timebox_active
        and effect.phase == development_timebox.guarded_entry
        and development_timebox.warning_seconds
        <= development_total_elapsed
        < development_timebox.duration_seconds
    ):
        logger.warning(
            "development-timebox warning: {:.0f}s consumed of {:.0f}s budget on phase {!r}",
            development_total_elapsed,
            development_timebox.duration_seconds,
            effect.phase,
        )
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
publish_cycle_deadline_env = _publish_cycle_deadline_env
withdraw_cycle_deadline_env = _withdraw_cycle_deadline_env
prompt_session_drain_for_phase = _prompt_session_drain_for_phase
