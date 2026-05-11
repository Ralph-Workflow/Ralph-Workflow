"""Verification phase handler.

The verification phase enforces a policy-defined gating check before the pipeline
can advance. The gate is declarative — it is validated at runtime against the
configured verification kind.

Verification kinds:
- artifact: the configured artifact path must exist and be non-empty
- none: purely declarative gate; always passes

On gate failure, when on_failure_route is set, the handler emits
PhaseFailureEvent(recoverable=False) so the reducer routes through
_enter_failed_recovery to the policy-declared failure route.
When on_failure_route is unset, the pipeline halts at the terminal failure route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from loguru import logger

from ralph.phases.required_artifacts import build_required_artifacts
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
    from ralph.policy.models import PhaseDefinition, PhaseVerificationPolicy


def _check_artifact_gate(
    ctx: PhaseContext,
    phase_name: str,
    phase_def: PhaseDefinition,
) -> tuple[bool, str | None]:
    """Check artifact verification gate.

    Returns:
        Tuple of (gate_passed, failure_reason). failure_reason is None if gate passed.
    """
    registry = build_required_artifacts(ctx.artifacts_policy)
    ra = registry.get(phase_def.drain)
    artifact_path = (
        ra.json_path if ra is not None else f".agent/artifacts/{phase_def.drain}_verification.json"
    )
    if not ctx.workspace.exists(artifact_path):
        return False, (
            f"Missing required verification artifact at '{artifact_path}'; "
            f"the agent must submit {phase_def.drain}_verification "
            f"before declaring completion"
        )
    try:
        content = ctx.workspace.read(artifact_path)
        if content and content.strip():
            return True, None
        return False, (
            f"Verification artifact at '{artifact_path}' is empty; "
            f"the artifact must contain verification evidence"
        )
    except Exception as exc:
        return False, (f"Verification artifact at '{artifact_path}' could not be read: {exc}")


def handle_verification_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Generic handler for verification-role phases.

    Dispatches on phase_def.verification.kind:
    - 'artifact': requires the configured artifact path to exist and be non-empty
    - 'none': purely declarative; emits AGENT_SUCCESS to advance

    On gate failure, when on_failure_route is set, emits
    PhaseFailureEvent(recoverable=False) so the reducer routes to that target.
    When on_failure_route is unset, recoverable=False halts at the terminal failure.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        return _handle_verification_invoke(effect, ctx)

    return []


def _handle_verification_invoke(
    effect: InvokeAgentEffect,
    ctx: PhaseContext,
) -> list[Event]:
    """Handle InvokeAgentEffect for verification phases."""
    phase_name = str(effect.phase)
    phase_def = ctx.pipeline_policy.phases.get(phase_name)

    if phase_def is None or phase_def.verification is None:
        logger.warning(
            "Verification phase '{}' has no verification policy; treating as pass-through",
            phase_name,
        )
        return [PipelineEvent.AGENT_SUCCESS]

    v = phase_def.verification
    gate_passed, failure_reason = _gate_result_for_kind(v, ctx, phase_name, phase_def)

    if gate_passed:
        return [PipelineEvent.AGENT_SUCCESS]

    return _emit_verification_failure(phase_name, failure_reason, v.on_failure_route)


def _assert_never(value: NoReturn) -> NoReturn:
    raise AssertionError(f"Unexpected value: {value!r}")


def _gate_result_for_kind(
    v: PhaseVerificationPolicy,
    ctx: PhaseContext,
    phase_name: str,
    phase_def: PhaseDefinition,
) -> tuple[bool, str | None]:
    """Compute gate result for a verification kind."""
    match v.kind:
        case "artifact":
            return _check_artifact_gate(ctx, phase_name, phase_def)
        case "none":
            return True, None
        case _ as unreachable:  # pragma: no cover
            _assert_never(unreachable)


def _emit_verification_failure(
    phase_name: str,
    failure_reason: str | None,
    on_failure_route: str | None,
) -> list[Event]:
    """Emit PhaseFailureEvent for verification gate failure."""
    reason = failure_reason or "verification gate failed"
    if on_failure_route:
        logger.warning(
            "Verification gate failed for phase '{}': {}. Routing to on_failure_route='{}'",
            phase_name,
            failure_reason,
            on_failure_route,
        )
    else:
        logger.warning(
            "Verification gate failed for phase '{}': {}. No on_failure_route set; "
            "pipeline will terminate at terminal failure.",
            phase_name,
            failure_reason,
        )
    return [
        PhaseFailureEvent(
            phase=phase_name,
            reason=reason,
            recoverable=False,
        )
    ]
