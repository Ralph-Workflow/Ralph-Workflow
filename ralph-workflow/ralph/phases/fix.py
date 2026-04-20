"""Fix phase handler.

The fix phase is invoked when review_analysis signals a loopback (request_changes).
It applies agent-suggested fixes to the codebase.
"""

from __future__ import annotations

import json

from loguru import logger

from ralph.phases import PhaseContext, register_handler
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent

FIX_RESULT_ARTIFACT_PATH = ".agent/artifacts/fix_result.json"


@register_handler("fix")
def handle_fix(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the fix phase.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        logger.info(
            "Fix phase: preparing prompt (iteration={})",
            effect.iteration,
        )
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        logger.info("Fix phase: processing fix result after agent run")
        try:
            artifact_wrapper = load_phase_artifact(ctx.workspace, FIX_RESULT_ARTIFACT_PATH)
            if artifact_wrapper.get("type") != "fix_result":
                raise PhaseArtifactError("Fix result artifact must declare type='fix_result'")
            unwrap_phase_artifact_content(
                artifact_wrapper,
                expected_type="fix_result",
            )
        except (json.JSONDecodeError, PhaseArtifactError, TypeError, ValueError) as exc:
            logger.warning("Fix phase missing fresh fix_result artifact: {}", exc)
            return [
                PhaseFailureEvent(
                    phase="fix",
                    reason=f"Missing/invalid fix_result artifact: {exc}",
                    recoverable=True,
                )
            ]
        return [PipelineEvent.AGENT_SUCCESS]

    return []
