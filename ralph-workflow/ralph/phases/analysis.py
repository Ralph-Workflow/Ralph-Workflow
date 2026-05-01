"""Shared analysis logic for parsing analysis decisions.

The analysis phase reads a typed artifact submitted by the agent via MCP
and extracts the decision field to route the pipeline.

Decision routing is driven entirely by policy: the phase's decisions table
in pipeline.toml maps raw status strings (from the agent artifact) to
PhaseDecisionRoute targets. The reducer routes via decisions[status].target
directly, so the raw status string is passed through as-is.

AnalysisDecision (from ralph.config.enums) is kept as a display/typing aid
only — it is NOT used for reducer routing.
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_PATH, PlanArtifactValidationError, is_noop_plan
from ralph.phases.artifacts import (
    PhaseArtifactError,
    decision_vocabulary_for_drain,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    build_retry_hint,
    resolve_required_artifact,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import (
    AnalysisDecisionEvent,
    Event,
    PhaseFailureEvent,
    PipelineEvent,
)

if TYPE_CHECKING:
    from ralph.phases import PhaseContext


def parse_analysis_decision_status(
    ctx: PhaseContext,
    drain_name: str,
    *,
    phase_name: str | None = None,
) -> str | None:
    """Parse the raw decision status string from the MCP artifact.

    Reads the artifact file from the workspace and extracts the status field.
    The raw status string is returned directly — the reducer looks up
    the target in ``phase_def.decisions[status].target``.

    The ``drain_name`` is used to locate the artifact and vocabulary; the
    ``phase_name`` (defaults to ``drain_name`` when omitted) is used to look up
    the phase's decisions table in pipeline policy.

    Args:
        ctx: Phase context with workspace and pipeline_policy.
        drain_name: Name of the drain (used for artifact path and vocabulary lookup).
        phase_name: Name of the phase in pipeline policy (defaults to drain_name).

    Returns:
        Raw status string, or None if parsing fails (caller should emit
        PhaseFailureEvent).
    """
    policy_phase_name = phase_name if phase_name is not None else drain_name
    artifact_type = f"{drain_name}_decision"
    ra = resolve_required_artifact(ctx.artifacts_policy, drain=drain_name)
    artifact_path = ra.json_path if ra is not None else f".agent/artifacts/{artifact_type}.json"

    if not ctx.workspace.exists(artifact_path):
        logger.warning(
            "No analysis artifact found at {}. Cannot determine decision status.",
            artifact_path,
        )
        return None

    try:
        artifact = load_phase_artifact(ctx.workspace, artifact_path)
        content = unwrap_phase_artifact_content(artifact, expected_type=artifact_type)
        status = str(content.get("status", "")).lower()

        vocabulary = decision_vocabulary_for_drain(ctx.artifacts_policy, drain_name, artifact_type)
        if vocabulary and status not in vocabulary:
            logger.warning(
                "Analysis artifact at {} used status '{}' outside allowed vocabulary {}.",
                artifact_path,
                status,
                vocabulary,
            )
            return None

        # Validate that the status exists in the policy decisions table.
        phase_def = ctx.pipeline_policy.phases.get(policy_phase_name)
        if phase_def is not None and phase_def.decisions and status not in phase_def.decisions:
            logger.warning(
                "Phase '{}' has no policy route for status '{}'. "
                "Add it to phases.{}.decisions or update the artifact decision_vocabulary.",
                policy_phase_name,
                status,
                policy_phase_name,
            )
            return None

        return status
    except Exception as exc:
        logger.warning(
            "Failed to parse analysis artifact at {}: {}.",
            artifact_path,
            exc,
        )
        return None


def _has_noop_plan(ctx: PhaseContext) -> bool:
    """Return True if a noop plan artifact is present in the workspace.

    Used to short-circuit analysis phases when the upstream execution produced
    no meaningful work (e.g. a planning artifact that declares skip=True).
    Only fires when the plan artifact exists and is parseable as a noop.
    """
    if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
        return False
    with suppress(
        json.JSONDecodeError,
        PlanArtifactValidationError,
        PhaseArtifactError,
        ValueError,
        Exception,
    ):
        wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
        raw = unwrap_phase_artifact_content(wrapper, expected_type="plan")
        return is_noop_plan(raw)
    return False


def handle_generic_analysis_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Generic handler for analysis-role phases registered via register_role_handlers.

    Used for policy-declared analysis phases whose names are not the canonical
    ``development_analysis`` or ``review_analysis``. The handler:

    - Uses ``effect.phase`` as the pipeline policy phase name.
    - Uses ``effect.drain`` (if set) or ``effect.phase`` as the drain name for
      artifact path and vocabulary lookup.
    - Emits ``AnalysisDecisionEvent`` with the raw decision string, letting
      the reducer route directly through ``phase_def.decisions[status].target``.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        phase_name = str(effect.phase)
        drain_name = effect.drain if effect.drain is not None else phase_name

        # Short-circuit when the upstream execution was a noop. No analysis
        # decision artifact will have been produced for a noop plan.
        if _has_noop_plan(ctx):
            logger.info("Analysis phase '{}': plan is a no-op — skipping analysis", phase_name)
            return [PipelineEvent.ANALYSIS_SUCCESS]

        ra = resolve_required_artifact(ctx.artifacts_policy, drain=drain_name)
        artifact_path = (
            ra.json_path if ra is not None
            else f".agent/artifacts/{drain_name}_decision.json"
        )

        if not ctx.workspace.exists(artifact_path):
            detail = (
                f"Missing required analysis artifact at {artifact_path}; "
                f"the agent must submit {drain_name}_decision before declaring completion"
            )
            logger.warning(
                "Analysis phase '{}' completed without required artifact at {}",
                phase_name,
                artifact_path,
            )
            with suppress(Exception):
                ctx.workspace.write(
                    retry_hint_path(phase_name),
                    build_retry_hint(phase_name, detail),
                )
            return [
                PhaseFailureEvent(
                    phase=phase_name,
                    reason=detail,
                    recoverable=True,
                    retry_in_session=True,
                )
            ]

        status = parse_analysis_decision_status(ctx, drain_name, phase_name=phase_name)
        if status is None:
            # parse_analysis_decision_status already logged the warning
            return [
                PhaseFailureEvent(
                    phase=phase_name,
                    reason=f"Unroutable analysis decision for phase '{phase_name}'",
                    recoverable=True,
                    retry_in_session=True,
                )
            ]

        logger.info("Analysis phase '{}' decision: {}", phase_name, status)
        return [AnalysisDecisionEvent(phase=phase_name, decision=status)]

    return []
