"""Fix phase handler.

The fix phase is invoked when review_analysis signals a loopback (request_changes).
It applies agent-suggested fixes to the codebase.
"""

from __future__ import annotations

import json
from contextlib import suppress

from loguru import logger

from ralph.phases import PhaseContext, register_handler
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    FIX_RESULT_ARTIFACT_JSON_PATH,
    build_retry_hint,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent

_FIX_RESULT_ARTIFACT_PATH = FIX_RESULT_ARTIFACT_JSON_PATH


def _write_retry_hint(ctx: PhaseContext, phase: str, detail: str) -> None:
    hint_path = retry_hint_path(phase)
    hint = build_retry_hint(phase, detail)
    with suppress(Exception):
        ctx.workspace.write(hint_path, hint)


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
        logger.info("Fix phase: validating fix_result artifact after agent run")
        if not ctx.workspace.exists(_FIX_RESULT_ARTIFACT_PATH):
            detail = (
                f"Missing fix_result artifact at {_FIX_RESULT_ARTIFACT_PATH}; "
                "the agent must submit fix_result before declaring completion"
            )
            logger.warning(
                "Fix agent completed without producing {}",
                _FIX_RESULT_ARTIFACT_PATH,
            )
            _write_retry_hint(ctx, "fix", detail)
            return [
                PhaseFailureEvent(
                    phase="fix",
                    reason=detail,
                    recoverable=True,
                    retry_in_session=True,
                )
            ]

        try:
            artifact_wrapper = load_phase_artifact(ctx.workspace, _FIX_RESULT_ARTIFACT_PATH)
            unwrap_phase_artifact_content(artifact_wrapper, expected_type="fix_result")
        except (json.JSONDecodeError, PhaseArtifactError, ValueError) as exc:
            detail = str(exc)
            logger.warning("Invalid fix_result artifact: {}", detail)
            _write_retry_hint(ctx, "fix", detail)
            return [
                PhaseFailureEvent(
                    phase="fix",
                    reason=f"Invalid fix_result artifact: {detail}",
                    recoverable=True,
                    retry_in_session=True,
                )
            ]

        return [PipelineEvent.AGENT_SUCCESS]

    return []
