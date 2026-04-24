"""Development phase handler.

The development phase invokes the development agent to implement code changes
based on the planning artifact. It may embed an analysis step that decides
whether to continue development or loop back for more iterations.

When the planning artifact is a typed no-op plan, the handler short-circuits
with ``AGENT_SUCCESS`` so the pipeline advances through development/commit
without invoking the development agent for a plan that asked for nothing.
"""

from __future__ import annotations

import json
from contextlib import suppress

from loguru import logger

from ralph.config.enums import AnalysisDecision
from ralph.mcp.artifacts.plan import (
    PLAN_ARTIFACT_PATH,
    PlanArtifactValidationError,
    is_noop_plan,
    normalize_plan_artifact_content,
)
from ralph.phases import PhaseContext, register_handler
from ralph.phases.analysis import parse_analysis_decision
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    DEV_ANALYSIS_DECISION_JSON_PATH,
    DEV_RESULT_ARTIFACT_JSON_PATH,
    build_missing_input_hint,
    build_retry_hint,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.work_units import WorkUnitsValidationError, parse_work_units_from_artifact
from ralph.policy.validation import PolicyValidationError, validate_work_units_against_policy


def _write_retry_hint(ctx: PhaseContext, phase: str, detail: str) -> None:
    hint_path = retry_hint_path(phase)
    hint = build_retry_hint(phase, detail)
    with suppress(Exception):
        ctx.workspace.write(hint_path, hint)


def _write_missing_input_hint(
    ctx: PhaseContext,
    phase: str,
    upstream_phase: str,
    artifact_path: str,
) -> None:
    hint_path = retry_hint_path(phase)
    hint = build_missing_input_hint(phase, upstream_phase, artifact_path)
    with suppress(Exception):
        ctx.workspace.write(hint_path, hint)


def _validate_plan_artifact(ctx: PhaseContext) -> list[Event] | None:
    """Validate the plan artifact. Returns failure events on error, None on success."""
    planning_artifact_path = PLAN_ARTIFACT_PATH
    if not ctx.workspace.exists(planning_artifact_path):
        reason = f"Missing planning artifact at {planning_artifact_path}"
        logger.warning(
            "Development phase missing required planning artifact at {}", planning_artifact_path
        )
        _write_missing_input_hint(ctx, "development", "planning", planning_artifact_path)
        return [PhaseFailureEvent(
            phase="development", reason=reason, recoverable=True, retry_in_session=True,
        )]
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, planning_artifact_path)
        artifact_content = unwrap_phase_artifact_content(artifact_wrapper, expected_type="plan")
        if is_noop_plan(artifact_content):
            return []  # empty list signals "noop — skip"
        artifact = (
            artifact_content
            if _is_legacy_work_units_payload(artifact_content)
            else normalize_plan_artifact_content(artifact_content)
        )
        parsed = parse_work_units_from_artifact(artifact)
        if parsed is not None:
            validate_work_units_against_policy(parsed, ctx.pipeline_policy)
    except (
        json.JSONDecodeError,
        PlanArtifactValidationError,
        PhaseArtifactError,
        ValueError,
        WorkUnitsValidationError,
        PolicyValidationError,
    ) as exc:
        logger.warning("Invalid development phase evidence: {}", exc)
        return [PhaseFailureEvent(
            phase="development",
            reason=f"Invalid development evidence: {exc}",
            recoverable=True,
            retry_in_session=True,
        )]
    return None  # success


def _validate_dev_result_artifact(ctx: PhaseContext) -> list[Event] | None:
    """Validate the development_result artifact. Returns failure events, None on success."""
    dev_result_path = DEV_RESULT_ARTIFACT_JSON_PATH
    if not ctx.workspace.exists(dev_result_path):
        detail = (
            f"Missing required artifact at {dev_result_path}; "
            "the agent must submit development_result before declaring completion"
        )
        logger.warning(
            "Development phase missing required development_result artifact at {}",
            dev_result_path,
        )
        _write_retry_hint(ctx, "development", detail)
        return [PhaseFailureEvent(
            phase="development", reason=detail, recoverable=True, retry_in_session=True,
        )]
    try:
        dev_result_wrapper = load_phase_artifact(ctx.workspace, dev_result_path)
        unwrap_phase_artifact_content(dev_result_wrapper, expected_type="development_result")
    except (json.JSONDecodeError, PhaseArtifactError, ValueError) as exc:
        detail = str(exc)
        logger.warning("Invalid development_result artifact: {}", detail)
        _write_retry_hint(ctx, "development", detail)
        return [PhaseFailureEvent(
            phase="development",
            reason=f"Invalid development_result artifact: {detail}",
            recoverable=True,
            retry_in_session=True,
        )]
    return None  # success


@register_handler("development")
def handle_development(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the development phase.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        logger.info("Development phase: preparing prompt (iteration={})", effect.iteration)
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        logger.info("Development phase: validating planning artifact after agent run")
        plan_result = _validate_plan_artifact(ctx)
        if plan_result is not None:
            return plan_result if plan_result else [PipelineEvent.AGENT_SUCCESS]
        dev_result = _validate_dev_result_artifact(ctx)
        if dev_result is not None:
            return dev_result
        return [PipelineEvent.AGENT_SUCCESS]

    return []


def _is_legacy_work_units_payload(content: dict[str, object]) -> bool:
    return "work_units" in content and "summary" not in content


def _development_plan_is_noop(ctx: PhaseContext) -> bool:
    if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
        return False
    try:
        wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
        raw = unwrap_phase_artifact_content(wrapper, expected_type="plan")
    except (
        json.JSONDecodeError,
        PlanArtifactValidationError,
        PhaseArtifactError,
        ValueError,
    ):
        return False
    return is_noop_plan(raw)


def _missing_analysis_artifact_event(phase: str, artifact_path: str) -> PhaseFailureEvent:
    return PhaseFailureEvent(
        phase=phase,
        reason=(
            "Missing required analysis artifact at "
            f"{artifact_path}; the agent must submit "
            f"{phase}_decision before declaring completion"
        ),
        recoverable=True,
        retry_in_session=True,
    )


def _analysis_event_for_decision(phase: str, decision: AnalysisDecision) -> list[Event]:
    if decision in (AnalysisDecision.PROCEED, AnalysisDecision.COMPLETE):
        return [PipelineEvent.ANALYSIS_SUCCESS]
    if decision in (
        AnalysisDecision.REVISE,
        AnalysisDecision.FAILURE,
        AnalysisDecision.ESCALATE,
    ):
        logger.warning("Analysis decision {} triggers loopback", decision)
        return [PipelineEvent.ANALYSIS_LOOPBACK]
    logger.warning("Unknown analysis decision: {}, defaulting to success", decision)
    return [PipelineEvent.ANALYSIS_SUCCESS]


@register_handler("development_analysis")
def handle_development_analysis(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the development analysis step (embedded in development phase).

    After the development agent completes, the analysis step reads the
    decision from the development analysis artifact and routes accordingly.

    When the planning artifact is a typed no-op plan, the handler short-circuits
    with ``ANALYSIS_SUCCESS`` so the pipeline advances through commit without
    trying to parse a non-existent development_analysis_decision artifact.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if not isinstance(effect, InvokeAgentEffect):
        return []

    if _development_plan_is_noop(ctx):
        logger.info("Development analysis: plan is a no-op — skipping analysis")
        return [PipelineEvent.ANALYSIS_SUCCESS]

    artifact_path = DEV_ANALYSIS_DECISION_JSON_PATH
    if not ctx.workspace.exists(artifact_path):
        detail = (
            "Missing required analysis artifact at "
            f"{artifact_path}; the agent must submit "
            "development_analysis_decision before declaring completion"
        )
        logger.warning(
            "Development analysis completed without required artifact at {}",
            artifact_path,
        )
        _write_retry_hint(ctx, "development_analysis", detail)
        return [_missing_analysis_artifact_event("development_analysis", artifact_path)]

    decision = parse_analysis_decision(ctx, "development_analysis")
    logger.info("Development analysis decision: {}", decision)
    return _analysis_event_for_decision("development_analysis", decision)
