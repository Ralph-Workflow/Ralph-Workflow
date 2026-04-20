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
from pathlib import Path

from loguru import logger

from ralph.config.enums import AnalysisDecision
from ralph.display.artifact_renderer import render_analysis_decision
from ralph.mcp.plan_artifact import (
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
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.work_units import WorkUnitsValidationError, parse_work_units_from_artifact
from ralph.policy.validation import PolicyValidationError, validate_work_units_against_policy

DEVELOPMENT_RESULT_ARTIFACT_PATH = ".agent/artifacts/development_result.json"


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
        logger.info(
            "Development phase: preparing prompt (iteration={})",
            effect.iteration,
        )
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        logger.info("Development phase: validating planning artifact after agent run")
        planning_artifact_path = PLAN_ARTIFACT_PATH
        if not ctx.workspace.exists(planning_artifact_path):
            logger.warning(
                "Development phase missing required planning artifact at {}", planning_artifact_path
            )
            return [
                PhaseFailureEvent(
                    phase="development",
                    reason=f"Missing planning artifact at {planning_artifact_path}",
                    recoverable=True,
                )
            ]

        try:
            artifact_wrapper = load_phase_artifact(ctx.workspace, planning_artifact_path)
            artifact_content = unwrap_phase_artifact_content(
                artifact_wrapper,
                expected_type="plan",
            )
            if is_noop_plan(artifact_content):
                logger.info("Development phase: plan is a no-op — skipping dev iteration")
                return [PipelineEvent.AGENT_SUCCESS]
            if _is_legacy_work_units_payload(artifact_content):
                artifact = artifact_content
            else:
                artifact = normalize_plan_artifact_content(artifact_content)
            parsed = parse_work_units_from_artifact(artifact)
            if parsed is not None:
                validate_work_units_against_policy(parsed, ctx.pipeline_policy)
            _require_development_result_artifact(ctx)
        except (
            json.JSONDecodeError,
            PlanArtifactValidationError,
            PhaseArtifactError,
            ValueError,
            WorkUnitsValidationError,
            PolicyValidationError,
        ) as exc:
            logger.warning("Invalid development phase evidence: {}", exc)
            return [
                PhaseFailureEvent(
                    phase="development",
                    reason=f"Invalid development evidence: {exc}",
                    recoverable=True,
                )
            ]
        return [PipelineEvent.AGENT_SUCCESS]

    return []


def _is_legacy_work_units_payload(content: dict[str, object]) -> bool:
    return "work_units" in content and "summary" not in content


def _require_development_result_artifact(ctx: PhaseContext) -> None:
    artifact_wrapper = load_phase_artifact(ctx.workspace, DEVELOPMENT_RESULT_ARTIFACT_PATH)
    if artifact_wrapper.get("type") != "development_result":
        raise PhaseArtifactError(
            "Development result artifact must declare type='development_result'"
        )
    unwrap_phase_artifact_content(
        artifact_wrapper,
        expected_type="development_result",
    )


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
    if isinstance(effect, InvokeAgentEffect):
        # Short-circuit if the plan artifact is a no-op — there is no analysis
        # decision artifact to parse because no development work was done.
        if ctx.workspace.exists(PLAN_ARTIFACT_PATH):
            try:
                wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
                raw = unwrap_phase_artifact_content(wrapper, expected_type="plan")
                if is_noop_plan(raw):
                    logger.info("Development analysis: plan is a no-op — skipping analysis")
                    return [PipelineEvent.ANALYSIS_SUCCESS]
            except (
                json.JSONDecodeError,
                PlanArtifactValidationError,
                PhaseArtifactError,
                ValueError,
            ):
                pass  # fall through to normal analysis decision parsing

        # Read the analysis artifact to determine routing
        decision = parse_analysis_decision(ctx, "development_analysis")
        logger.info("Development analysis decision: {}", decision)

        # Render the analysis decision block for the user
        if ctx.console is not None:
            workspace_root = Path(ctx.workspace.absolute_path("."))
            render_analysis_decision(workspace_root, "development_analysis", ctx.console)

        if decision in (AnalysisDecision.PROCEED, AnalysisDecision.COMPLETE):
            return [PipelineEvent.ANALYSIS_SUCCESS]
        elif decision == AnalysisDecision.REVISE:
            return [PipelineEvent.ANALYSIS_LOOPBACK]
        elif decision in (AnalysisDecision.FAILURE, AnalysisDecision.ESCALATE):
            logger.warning("Analysis decision {} triggers pipeline failure", decision)
            return [
                PhaseFailureEvent(
                    phase="development_analysis",
                    reason=f"Analysis decision: {decision}",
                    recoverable=False,
                )
            ]
        else:
            logger.warning("Unknown analysis decision: {}, defaulting to success", decision)
            return [PipelineEvent.ANALYSIS_SUCCESS]

    return []
