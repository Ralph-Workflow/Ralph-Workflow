"""Shared analysis logic for parsing analysis decisions.

The analysis phase reads a typed artifact submitted by the agent via MCP
and extracts the decision field to route the pipeline.

Analysis decisions are explicit typed enums (AnalysisDecision) that drive
the reducer routing for analysis-role phases.

Decision routing is driven entirely by policy: the phase's decisions table
in pipeline.toml maps status strings to routing targets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.enums import PHASE_FAILED, AnalysisDecision
from ralph.phases.artifacts import (
    decision_vocabulary_for_drain,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import REQUIRED_ARTIFACTS
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
    from ralph.policy.models import PhaseDecisionRoute


def parse_analysis_decision(
    ctx: PhaseContext,
    drain_name: str,
    *,
    phase_name: str | None = None,
) -> AnalysisDecision:
    """Parse the analysis decision from the MCP artifact.

    Reads the artifact file from the workspace and extracts the status field,
    which is mapped to an AnalysisDecision enum value via the phase's decisions
    table in policy.

    The ``drain_name`` is used to locate the artifact and vocabulary; the
    ``phase_name`` (defaults to ``drain_name`` when omitted) is used to look up
    the phase's decisions table in pipeline policy. Pass an explicit ``phase_name``
    when the policy phase name differs from the drain name (e.g. policy-renamed
    analysis phases).

    Args:
        ctx: Phase context with workspace and pipeline_policy.
        drain_name: Name of the drain (used for artifact path and vocabulary lookup).
        phase_name: Name of the phase in pipeline policy (defaults to drain_name).

    Returns:
        AnalysisDecision enum value. Defaults to FAILURE if parsing fails.
    """
    policy_phase_name = phase_name if phase_name is not None else drain_name
    artifact_type = f"{drain_name}_decision"
    ra = REQUIRED_ARTIFACTS.get(drain_name)
    artifact_path = ra.json_path if ra is not None else f".agent/artifacts/{artifact_type}.json"

    if not ctx.workspace.exists(artifact_path):
        logger.warning(
            "No analysis artifact found at {}. Defaulting to FAILURE.",
            artifact_path,
        )
        return AnalysisDecision.FAILURE

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
            return AnalysisDecision.FAILURE

        phase_def = ctx.pipeline_policy.phases.get(policy_phase_name)
        if phase_def is not None and phase_def.decisions:
            return _resolve_decision_from_policy(status, phase_def.decisions, artifact_path)

        logger.warning(
            "Phase '{}' has no decisions table in policy; status '{}' cannot be routed."
            " Defaulting to FAILURE.",
            policy_phase_name,
            status,
        )
        return AnalysisDecision.FAILURE
    except Exception as exc:
        logger.warning(
            "Failed to parse analysis artifact at {}: {}. Defaulting to FAILURE.",
            artifact_path,
            exc,
        )
        return AnalysisDecision.FAILURE


def _resolve_decision_from_policy(
    status: str,
    decisions: dict[str, PhaseDecisionRoute],
    artifact_path: str,
) -> AnalysisDecision:
    """Resolve AnalysisDecision from policy decisions table using structural route properties.

    The decision is derived from the matched PhaseDecisionRoute:
    - reset_loop=True  → PROCEED (forward exit, loop counter resets on this transition)
    - target==PHASE_FAILED → FAILURE (terminal failure route)
    - otherwise → REVISE (loopback to a correction phase)

    Status values not found in the decisions table map to FAILURE.
    """
    route = decisions.get(status)
    if route is None:
        logger.warning(
            "Status '{}' not in policy decisions table at {}. Defaulting to FAILURE.",
            status,
            artifact_path,
        )
        return AnalysisDecision.FAILURE

    if route.reset_loop:
        return AnalysisDecision.PROCEED
    if route.target == PHASE_FAILED:
        return AnalysisDecision.FAILURE
    return AnalysisDecision.REVISE


def handle_generic_analysis_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Generic handler for analysis-role phases registered via register_role_handlers.

    Used for policy-declared analysis phases whose names are not the canonical
    ``development_analysis`` or ``review_analysis``. The handler:

    - Uses ``effect.phase`` as the pipeline policy phase name.
    - Uses ``effect.drain`` (if set) or ``effect.phase`` as the drain name for
      artifact path and vocabulary lookup.
    - Emits ``ANALYSIS_SUCCESS`` on a forward-exit decision (reset_loop=True) and
      ``ANALYSIS_LOOPBACK`` on a revise/failure decision.

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

        ra = REQUIRED_ARTIFACTS.get(drain_name)
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
            return [
                PhaseFailureEvent(
                    phase=phase_name,
                    reason=detail,
                    recoverable=True,
                    retry_in_session=True,
                )
            ]

        decision = parse_analysis_decision(ctx, drain_name, phase_name=phase_name)
        logger.info("Analysis phase '{}' decision: {}", phase_name, decision)

        if decision in (AnalysisDecision.PROCEED, AnalysisDecision.COMPLETE):
            return [PipelineEvent.ANALYSIS_SUCCESS]
        if decision in (
            AnalysisDecision.REVISE,
            AnalysisDecision.FAILURE,
            AnalysisDecision.ESCALATE,
        ):
            logger.warning(
                "Analysis phase '{}' decision {} triggers loopback", phase_name, decision
            )
            return [PipelineEvent.ANALYSIS_LOOPBACK]
        logger.warning(
            "Unknown analysis decision: {} for phase '{}'. Defaulting to success.",
            decision,
            phase_name,
        )
        return [PipelineEvent.ANALYSIS_SUCCESS]

    return []
