"""Phase transition display logic for the pipeline runner."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.config.enums import Verbosity
from ralph.display.parallel_display import (
    ParallelDisplay,
    get_display_context,
)
from ralph.display.phase_banner import (
    show_phase_close_banner,
    show_phase_start_from_entry,
    show_phase_transition,
)
from ralph.display.phase_lifecycle import ExitContext, PhaseEntryModel, PhaseExitModel
from ralph.pipeline import progress
from ralph.pipeline._phase_change_render_data import _PhaseChangeRenderData
from ralph.pipeline._phase_transition_summary import emit_final_summary as _emit_final_summary
from ralph.pipeline.events import AnalysisDecisionEvent, Event, PipelineEvent
from ralph.pipeline.handoffs import (
    ExhaustedAnalysisBypassResult,
    resolve_exhausted_analysis_bypass,
    resolve_next_phase,
)
from ralph.pipeline.phase_rendering import VERBOSITY_RANK, verbosity_rank
from ralph.prompts.debug_dump import multimodal_sidecar_path, prompt_dump_path

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy
    from ralph.workspace import FsWorkspace


if TYPE_CHECKING:

    class _PhaseCountersProtocol(Protocol):
        content_blocks: int
        thinking_blocks: int
        tool_calls: int
        errors: int

    class _ShowCloseBannerFn(Protocol):
        def __call__(
            self,
            exit_model: PhaseExitModel,
            *,
            display_context: DisplayContext,
            pipeline_policy: PipelinePolicy | None = ...,
        ) -> None: ...

    class _ShowTransitionFn(Protocol):
        def __call__(
            self,
            from_phase: str,
            to_phase: str,
            *,
            context: dict[str, object] | None = ...,
            display_context: DisplayContext,
            pipeline_policy: PipelinePolicy | None = ...,
        ) -> None: ...


def _find_commit_counter_from_phase(
    phase_name: str,
    policy: PipelinePolicy,
) -> str | None:
    """Trace on_success transitions to the nearest lifecycle or commit counter owner.

    Returns the lifecycle-owned counter name when the phase graph declares one,
    otherwise falls back to the nearest commit phase increments_counter.
    """
    visited: set[str] = set()
    current: str | None = phase_name
    while current and current not in visited:
        visited.add(current)
        phase_def = policy.phases.get(current)
        if phase_def is None:
            break
        lifecycle = policy.lifecycle_phases.get(current)
        if lifecycle is not None:
            counter = lifecycle.increments_counter
            return counter if counter and counter != "none" else None
        if phase_def.role == "commit" and phase_def.commit_policy is not None:
            counter = phase_def.commit_policy.increments_counter
            return counter if counter and counter != "none" else None
        current = phase_def.transitions.on_success if phase_def.transitions else None
    return None


def _resolve_analysis_cap(
    iteration_field: str,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> int:
    return progress.resolve_analysis_cap(state, iteration_field, pipeline_policy)


def _build_phase_entry_model_from_state(
    phase: str,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    *,
    agent_name: str | None = None,
) -> PhaseEntryModel:
    """Build the canonical phase-entry model from pipeline state."""
    phase_role: str | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None
    phase_def = pipeline_policy.phases.get(phase)
    if phase_def is not None:
        phase_role = phase_def.role
        if phase_def.role == "analysis" and phase_def.loop_policy is not None:
            field = phase_def.loop_policy.iteration_state_field
            inner_analysis = state.get_loop_iteration(field) + 1
            inner_analysis_cap = _resolve_analysis_cap(field, state, pipeline_policy)

    outer_iteration: int | None = None
    outer_dev_cap: int | None = None
    counter = _find_commit_counter_from_phase(phase, pipeline_policy)
    if counter is not None:
        outer_iteration = state.get_outer_progress(counter)
        outer_dev_cap = state.get_budget_cap(counter)

    current_dev_cycle = outer_iteration + 1 if outer_iteration is not None else None
    return PhaseEntryModel(
        phase_name=phase,
        phase_role=phase_role,
        agent_name=agent_name,
        outer_dev_iteration=current_dev_cycle,
        outer_dev_cap=outer_dev_cap,
        inner_analysis=inner_analysis,
        inner_analysis_cap=inner_analysis_cap,
    )


def _show_phase_start_with_context(
    phase: str,
    agent_name: str,
    display_context: DisplayContext,
    state: PipelineState,
    *,
    pipeline_policy: PipelinePolicy,
) -> None:
    """Display the canonical model-based phase-start banner for the live runner."""
    entry = _build_phase_entry_model_from_state(
        phase,
        state,
        pipeline_policy,
        agent_name=agent_name,
    )
    show_phase_start_from_entry(
        entry, display_context=display_context, pipeline_policy=pipeline_policy
    )


@dataclass(frozen=True)
class _PendingPhaseTransitionMetadata:
    previous_phase: str
    current_phase: str
    transition_context: dict[str, object] | None = None
    routing_note: str | None = None
    skipped_phases: tuple[str, ...] = ()


_PENDING_PHASE_TRANSITION_METADATA_ATTR = "_pending_phase_transition_metadata"


def _analysis_phase_label(phase: str) -> str:
    return phase.replace("_", " ").title()


def _humanize_analysis_decision(decision: str) -> str:
    return decision.replace("_", " ")


def _bypass_info_message(phase: str) -> str:
    return f"{_analysis_phase_label(phase)} cap reached, skipping"


def _bypass_transition_context(
    bypass: ExhaustedAnalysisBypassResult,
) -> dict[str, object] | None:
    if not bypass.skipped:
        return None
    return {_analysis_phase_label(skip.phase): "cap reached, skipping" for skip in bypass.skipped}


def _store_pending_phase_transition_metadata(
    display: ParallelDisplay | None,
    metadata: _PendingPhaseTransitionMetadata | None,
) -> None:
    if display is None:
        return
    if metadata is None:
        with suppress(Exception):
            delattr(display, _PENDING_PHASE_TRANSITION_METADATA_ATTR)
        return
    with suppress(Exception):
        setattr(display, _PENDING_PHASE_TRANSITION_METADATA_ATTR, metadata)


def _consume_pending_phase_transition_metadata(
    display: ParallelDisplay,
    previous_phase: str,
    current_phase: str,
) -> _PendingPhaseTransitionMetadata | None:
    metadata = cast(
        "_PendingPhaseTransitionMetadata | None",
        getattr(display, _PENDING_PHASE_TRANSITION_METADATA_ATTR, None),
    )
    if metadata is None:
        return None
    with suppress(Exception):
        delattr(display, _PENDING_PHASE_TRANSITION_METADATA_ATTR)
    if metadata.previous_phase != previous_phase or metadata.current_phase != current_phase:
        return None
    return metadata


def _analysis_decision_transition_context(
    event: AnalysisDecisionEvent,
    next_state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> dict[str, object] | None:
    context: dict[str, object] = {"decision": _humanize_analysis_decision(event.decision)}
    phase_def = pipeline_policy.phases.get(event.phase)
    if phase_def is not None and phase_def.loop_policy is not None:
        route = phase_def.decisions.get(event.decision)
        if route is not None and not route.reset_loop:
            iteration_field = phase_def.loop_policy.iteration_state_field
            analysis_cur = next_state.get_loop_iteration(iteration_field)
            max_iter = _resolve_analysis_cap(iteration_field, next_state, pipeline_policy)
            if progress.is_final_analysis_iteration(analysis_cur, max_iter):
                context["analysis_status"] = "final, skipping next"
    return context or None


def _bypass_resolution_for_transition(
    state: PipelineState,
    event: Event,
    next_state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> ExhaustedAnalysisBypassResult | None:
    phase_def = pipeline_policy.phases.get(state.phase)
    if phase_def is None:
        return None

    candidate_phase: str | None = None
    if event == PipelineEvent.PHASE_ADVANCE and phase_def.role == "analysis":
        candidate_phase = state.phase
    elif event in (
        PipelineEvent.AGENT_SUCCESS,
        PipelineEvent.ANALYSIS_SUCCESS,
        PipelineEvent.COMMIT_SUCCESS,
        PipelineEvent.COMMIT_SKIPPED,
        PipelineEvent.FIX_SUCCESS,
        PipelineEvent.REVIEW_CLEAN,
    ):
        with suppress(ValueError):
            candidate_phase = resolve_next_phase(state.phase, "success", pipeline_policy)
    if candidate_phase is None:
        return None

    with suppress(TypeError, AttributeError):
        bypass = resolve_exhausted_analysis_bypass(state, candidate_phase, pipeline_policy)
        if not bypass.skipped or next_state.phase != bypass.target_phase:
            return None
        return bypass
    return None


def _record_phase_transition_metadata(
    display: ParallelDisplay | None,
    state: PipelineState,
    event: Event,
    next_state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> tuple[str, ...]:
    if isinstance(event, AnalysisDecisionEvent):
        metadata = _PendingPhaseTransitionMetadata(
            previous_phase=state.phase,
            current_phase=next_state.phase,
            transition_context=_analysis_decision_transition_context(
                event,
                next_state,
                pipeline_policy,
            ),
        )
        _store_pending_phase_transition_metadata(display, metadata)
        return ()

    bypass = _bypass_resolution_for_transition(state, event, next_state, pipeline_policy)
    if bypass is None:
        _store_pending_phase_transition_metadata(display, None)
        return ()

    skipped_phases = tuple(skip.phase for skip in bypass.skipped)
    metadata = _PendingPhaseTransitionMetadata(
        previous_phase=state.phase,
        current_phase=next_state.phase,
        transition_context=_bypass_transition_context(bypass),
        routing_note="; ".join(_bypass_info_message(skip.phase) for skip in bypass.skipped),
        skipped_phases=skipped_phases,
    )
    _store_pending_phase_transition_metadata(display, metadata)
    return skipped_phases


def _phase_transition_context(
    previous_phase: str,
    current_phase: str,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    metadata: _PendingPhaseTransitionMetadata | None = None,
) -> dict[str, object] | None:
    if metadata is not None:
        return metadata.transition_context

    previous_phase_def = pipeline_policy.phases.get(previous_phase)
    if previous_phase_def is None or previous_phase_def.role != "analysis":
        return None

    loop_policy = previous_phase_def.loop_policy
    if loop_policy is None:
        return None

    iteration_field = loop_policy.iteration_state_field
    analysis_cur = state.get_loop_iteration(iteration_field)
    max_iter = _resolve_analysis_cap(iteration_field, state, pipeline_policy)
    if progress.is_final_analysis_iteration(analysis_cur, max_iter):
        return {"analysis_status": "final, skipping next"}
    return None


def _skipped_exhausted_analysis_info(
    previous_phase: str,
    current_phase: str,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> tuple[str, str] | None:
    previous_phase_def = pipeline_policy.phases.get(previous_phase)
    if previous_phase_def is None:
        return None

    candidate_phase = previous_phase_def.transitions.on_success
    if not candidate_phase:
        return None

    bypass = resolve_exhausted_analysis_bypass(state, candidate_phase, pipeline_policy)
    if not bypass.skipped or bypass.target_phase != current_phase:
        return None

    skipped_phase = bypass.skipped[0].phase
    return (skipped_phase, _bypass_info_message(skipped_phase))


def _build_phase_change_render_data(
    display: ParallelDisplay,
    previous_phase: str,
    state: PipelineState,
    *,
    pipeline_policy: PipelinePolicy,
) -> _PhaseChangeRenderData:
    elapsed = (
        display.last_phase_elapsed_seconds
    )
    waiting_status_line = (
        display.subscriber.waiting_status_line
    )
    content_blocks = 0
    thinking_blocks = 0
    tool_calls = 0
    errors = 0
    phase_counters = cast(
        "_PhaseCountersProtocol | None", getattr(display, "last_phase_counters", None)
    )
    if phase_counters is not None:
        content_blocks = phase_counters.content_blocks
        thinking_blocks = phase_counters.thinking_blocks
        tool_calls = phase_counters.tool_calls
        errors = phase_counters.errors
    artifact_outcome = ""
    raw_outcome = cast("str | None", getattr(display, "last_phase_artifact_outcome", None))
    artifact_outcome = raw_outcome if raw_outcome else ""
    entry = _build_phase_entry_model_from_state(previous_phase, state, pipeline_policy)
    prev_phase_def = pipeline_policy.phases.get(previous_phase)
    prev_phase_role: str | None = prev_phase_def.role if prev_phase_def is not None else None
    pending_metadata = _consume_pending_phase_transition_metadata(
        display,
        previous_phase,
        state.phase,
    )
    skipped_info = None
    if pending_metadata is None:
        skipped_info = _skipped_exhausted_analysis_info(
            previous_phase,
            state.phase,
            state,
            pipeline_policy,
        )
    routing_note: str | None = (
        pending_metadata.routing_note
        if pending_metadata is not None
        else (skipped_info[1] if skipped_info is not None else None)
    )
    exit_model = PhaseExitModel.from_entry_model(
        entry,
        ExitContext(
            elapsed_seconds=elapsed,
            exit_trigger="produced" if artifact_outcome else "completed",
            content_blocks=content_blocks,
            thinking_blocks=thinking_blocks,
            tool_calls=tool_calls,
            errors=errors,
            artifact_outcome=artifact_outcome,
            review_issues_found=(
                progress.review_issues_found(state, pipeline_policy)
                if prev_phase_role == "review"
                else None
            ),
            routing_note=routing_note,
            waiting_status_line=waiting_status_line,
            last_failure_category=state.last_failure_category,
        ),
    )
    return _PhaseChangeRenderData(
        previous_phase=previous_phase,
        current_phase=state.phase,
        exit_model=exit_model,
        transition_context=_phase_transition_context(
            previous_phase,
            state.phase,
            state,
            pipeline_policy,
            pending_metadata,
        ),
    )


def _clear_phase_materialization_outputs(workspace: FsWorkspace, phase: str) -> None:
    """Remove stale prompt-materialization outputs for a phase when it is skipped."""
    for path in (prompt_dump_path(phase), multimodal_sidecar_path(phase)):
        with suppress(Exception):
            workspace.remove(path)


def _emit_phase_change_surfaces(
    display: ParallelDisplay,
    render_data: _PhaseChangeRenderData,
    *,
    display_context: DisplayContext,
    pipeline_policy: PipelinePolicy,
    show_close_banner_fn: _ShowCloseBannerFn | None = None,
    show_transition_fn: _ShowTransitionFn | None = None,
) -> None:
    phase_close_already_emitted_attr: object = getattr(
        display, "phase_close_emitted", False
    )
    phase_close_already_emitted: bool = bool(phase_close_already_emitted_attr)
    if not phase_close_already_emitted and hasattr(display, "emit_phase_close_from_exit"):
        with suppress(Exception):
            display.emit_phase_close_from_exit(render_data.exit_model)
    _close_fn = show_close_banner_fn or show_phase_close_banner
    with suppress(Exception):
        _close_fn(
            render_data.exit_model,
            display_context=display_context,
            pipeline_policy=pipeline_policy,
        )
    _trans_fn = show_transition_fn or show_phase_transition
    with suppress(Exception):
        _trans_fn(
            render_data.previous_phase,
            render_data.current_phase,
            context=render_data.transition_context,
            display_context=display_context,
            pipeline_policy=pipeline_policy,
        )


def _emit_phase_transition_if_changed(
    display: ParallelDisplay,
    previous_phase: str,
    state: PipelineState,
    *,
    verbosity: Verbosity,
    pipeline_policy: PipelinePolicy,
    show_close_banner_fn: _ShowCloseBannerFn | None = None,
    show_transition_fn: _ShowTransitionFn | None = None,
) -> str:
    """Emit the canonical close+transition display when the phase changes.

    Returns the new previous_phase value (always state.phase). Quiet mode
    is a no-op except for state tracking.
    """
    if state.phase == previous_phase:
        return previous_phase
    if verbosity_rank(verbosity) <= VERBOSITY_RANK[Verbosity.QUIET]:
        return state.phase

    ctx = get_display_context(display)

    try:
        render_data = _build_phase_change_render_data(
            display,
            previous_phase,
            state,
            pipeline_policy=pipeline_policy,
        )
        _emit_phase_change_surfaces(
            display,
            render_data,
            display_context=ctx,
            pipeline_policy=pipeline_policy,
            show_close_banner_fn=show_close_banner_fn,
            show_transition_fn=show_transition_fn,
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("phase change emission failed", exc_info=True)

    return state.phase


find_commit_counter_from_phase = _find_commit_counter_from_phase
build_phase_entry_model_from_state = _build_phase_entry_model_from_state
show_phase_start_with_context = _show_phase_start_with_context
PendingPhaseTransitionMetadata = _PendingPhaseTransitionMetadata
PENDING_PHASE_TRANSITION_METADATA_ATTR = _PENDING_PHASE_TRANSITION_METADATA_ATTR
record_phase_transition_metadata = _record_phase_transition_metadata
skipped_exhausted_analysis_info = _skipped_exhausted_analysis_info
clear_phase_materialization_outputs = _clear_phase_materialization_outputs
emit_final_summary = _emit_final_summary
emit_phase_transition_if_changed = _emit_phase_transition_if_changed

__all__ = [
    "PENDING_PHASE_TRANSITION_METADATA_ATTR",
    "PendingPhaseTransitionMetadata",
    "build_phase_entry_model_from_state",
    "clear_phase_materialization_outputs",
    "emit_final_summary",
    "emit_phase_transition_if_changed",
    "find_commit_counter_from_phase",
    "record_phase_transition_metadata",
    "show_phase_start_with_context",
    "skipped_exhausted_analysis_info",
]
