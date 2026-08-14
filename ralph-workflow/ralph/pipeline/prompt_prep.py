"""Prompt materialization helpers for the pipeline runner."""

from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.protocol.capability_mapping import DrainClass, SessionDrain
from ralph.mcp.protocol.env import (
    CYCLE_DEADLINE_EPOCH_ENV,
    CYCLE_DURATION_SECONDS_ENV,
    CYCLE_FINALIZATION_TARGET_ENV,
    CYCLE_WARN_EPOCH_ENV,
    WORKER_NAMESPACE_ENV,
)
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
    cycle_deadline_epochs,
    cycle_timebox_warning,
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
    media_entries = collect_media_entries_for_phase(workspace, effect.phase, drain=phase_drain) or None
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
        cycle_timebox_warning=_worker_cycle_timebox_warning(
            state, effect.phase, pipeline_policy
        ),
    )


def _worker_cycle_timebox_warning(
    state: PipelineState | None,
    target_phase: str,
    pipeline_policy: PipelinePolicy,
) -> dict[str, object] | None:
    """Return the timebox warning for a prepared prompt, if one is due.

    A prompt prepared ahead of its invocation has no routing timing to sample,
    so the cycle's consumed seconds carried on the state stand in for elapsed
    time. That omits only the current step's in-flight delta, which is what
    the agent-invocation path adds. Fan-out workers do NOT come through here —
    they run in their own process from a manifest and rebuild the warning from
    the published environment instead (see
    :func:`_cycle_timebox_warning_from_env`).
    """
    if state is None:
        return None
    return cycle_timebox_warning(
        state,
        target_phase,
        policy=pipeline_policy,
        routing_timing=RoutingTiming(
            monotonic_now=0.0,
            total_elapsed_seconds=state.cycle_timebox_consumed_seconds,
        ),
    )



def _withdraw_cycle_deadline_env() -> None:
    """Remove any published cycle deadline from the environment.

    The environment is process-global, so a deadline left published outlives
    the invocation it describes and is inherited by every subprocess spawned
    afterwards — including agents that have nothing to do with the cycle, such
    as the auto-integrate conflict resolver, which would be nagged to wrap up
    work it never started.
    """
    for name in (
        CYCLE_WARN_EPOCH_ENV,
        CYCLE_DEADLINE_EPOCH_ENV,
        CYCLE_DURATION_SECONDS_ENV,
        CYCLE_FINALIZATION_TARGET_ENV,
    ):
        os.environ.pop(name, None)


def _publish_cycle_deadline_env(
    state: PipelineState,
    target_phase: str,
    policy_bundle: PolicyBundle,
    cycle_total_elapsed: float | None,
) -> None:
    """Publish (or withdraw) the cycle deadline for the invocation about to start.

    The MCP server is a separate process spawned per invocation, so it reads
    the deadline from the environment it inherits rather than from pipeline
    state. Withdrawing is as important as publishing: without it a later
    invocation outside the cycle would inherit a stale deadline and nag the
    agent about a budget that no longer applies.
    """
    routing_timing = (
        RoutingTiming(monotonic_now=0.0, total_elapsed_seconds=cycle_total_elapsed)
        if cycle_total_elapsed is not None
        else None
    )
    published = cycle_deadline_epochs(
        state,
        target_phase,
        policy=policy_bundle.pipeline,
        routing_timing=routing_timing,
        now_epoch=time.time(),
    )
    if published is None:
        _withdraw_cycle_deadline_env()
        return
    warn_epoch, deadline_epoch, finalization_target = published
    os.environ[CYCLE_WARN_EPOCH_ENV] = repr(warn_epoch)
    os.environ[CYCLE_DEADLINE_EPOCH_ENV] = repr(deadline_epoch)
    os.environ[CYCLE_FINALIZATION_TARGET_ENV] = finalization_target
    duration = policy_bundle.pipeline.cycle_timebox
    if duration is not None:
        os.environ[CYCLE_DURATION_SECONDS_ENV] = repr(duration.duration_seconds)



def _env_seconds(env: Mapping[str, str], name: str) -> float | None:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _cycle_timebox_warning_from_env(
    env: Mapping[str, str],
    *,
    now_epoch: float,
) -> dict[str, object] | None:
    """Rebuild the timebox warning from the deadline the parent published.

    A fan-out worker runs in its own process from a manifest and its pipeline
    state carries no cycle timing, so the state-based warning the serial path
    uses is always empty for it. The published epochs it inherits are the only
    thing that crosses that boundary, so the worker's prompt appendix is
    derived from them. Returns ``None`` when nothing was published, when the
    warning point is still ahead, or when any published value is unusable.
    """
    warn_epoch = _env_seconds(env, CYCLE_WARN_EPOCH_ENV)
    deadline_epoch = _env_seconds(env, CYCLE_DEADLINE_EPOCH_ENV)
    duration = _env_seconds(env, CYCLE_DURATION_SECONDS_ENV)
    if warn_epoch is None or deadline_epoch is None or duration is None:
        return None
    if now_epoch < warn_epoch:
        return None
    remaining = max(0.0, deadline_epoch - now_epoch)
    return {
        "elapsed_seconds": max(0.0, duration - remaining),
        "remaining_seconds": remaining,
        "duration_seconds": duration,
        "finalization_target": env.get(CYCLE_FINALIZATION_TARGET_ENV) or "the final commit",
    }


def _materialize_agent_prompt_if_needed(
    effect: Effect,
    state: PipelineState,
    workspace: FsWorkspace,
    policy_bundle: PolicyBundle,
    registry: _RegistryLike,
    *,
    materialize_fn: _MaterializePromptFn | None = None,
    cycle_total_elapsed: float | None = None,
) -> None:
    if not isinstance(effect, InvokeAgentEffect):
        return

    _publish_cycle_deadline_env(state, effect.phase, policy_bundle, cycle_total_elapsed)

    agent = registry.get(effect.agent_name)
    agent_drain = effect.drain or resolve_phase_drain(effect.phase, policy_bundle.pipeline) or effect.phase
    media_entries = collect_media_entries_for_phase(workspace, effect.phase, drain=agent_drain) or None
    # Build the optional cycle-timebox warning payload for the guarded
    # development entry when elapsed >= 80% of the configured duration.
    _warning_data = None
    if cycle_total_elapsed is not None:
        _warning_data = cycle_timebox_warning(
            state,
            effect.phase,
            policy=policy_bundle.pipeline,
            routing_timing=RoutingTiming(
                monotonic_now=0.0,
                total_elapsed_seconds=cycle_total_elapsed,
            ),
        )
    if _warning_data is not None:
        _raw_elapsed = _warning_data.get("elapsed_seconds", 0.0)
        _raw_duration = _warning_data.get("duration_seconds", 0.0)
        _elapsed = float(_raw_elapsed) if isinstance(_raw_elapsed, (int, float)) else 0.0
        _duration = float(_raw_duration) if isinstance(_raw_duration, (int, float)) else 0.0
        _pct = (_elapsed / _duration * 100.0) if _duration > 0 else 0.0
        logger.warning(
            "cycle-timebox warning: {:.0f}s consumed of {:.0f}s budget "
            "({:.0f}% elapsed) on phase {!r}",
            _elapsed,
            _duration,
            _pct,
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
        cycle_timebox_warning=_warning_data,
    )


materialize_prepared_prompt = _materialize_prepared_prompt
materialize_agent_prompt_if_needed = _materialize_agent_prompt_if_needed
publish_cycle_deadline_env = _publish_cycle_deadline_env
cycle_timebox_warning_from_env = _cycle_timebox_warning_from_env
withdraw_cycle_deadline_env = _withdraw_cycle_deadline_env
prompt_session_drain_for_phase = _prompt_session_drain_for_phase
