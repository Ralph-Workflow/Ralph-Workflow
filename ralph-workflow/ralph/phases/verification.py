
from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from loguru import logger

from ralph.phases.artifact_freshness import (
    is_stale_since_invocation,
    stale_artifact_detail,
)
from ralph.phases.artifacts import legacy_json_rejection_detail
from ralph.phases.required_artifacts import build_required_artifacts, clear_validation_retry_hint
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
    registry = build_required_artifacts(ctx.artifacts_policy)
    ra = registry.get(phase_def.drain)
    artifact_path = (
        ra.artifact_path
        if ra is not None
        else f".agent/artifacts/{phase_def.drain}_verification.md"
    )
    if not ctx.workspace.exists(artifact_path):
        legacy_detail = legacy_json_rejection_detail(ctx.workspace, artifact_path)
        if legacy_detail is not None:
            return False, legacy_detail
        return False, (
            f"Missing required verification artifact at '{artifact_path}'; "
            f"the agent must submit {phase_def.drain}_verification "
            f"before declaring completion"
        )
    if is_stale_since_invocation(ctx.workspace, phase_name, artifact_path):
        return False, stale_artifact_detail(artifact_path, f"{phase_def.drain}_verification")
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
    if isinstance(effect, PreparePromptEffect):
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        return _handle_verification_invoke(effect, ctx)

    return []


def _handle_verification_invoke(
    effect: InvokeAgentEffect,
    ctx: PhaseContext,
) -> list[Event]:
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
        clear_validation_retry_hint(
            ctx.workspace,
            phase_name,
            pipeline_policy=ctx.pipeline_policy,
        )
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
    match v.kind:
        case "artifact":
            return _check_artifact_gate(ctx, phase_name, phase_def)
        case "none":
            return True, None
        case _ as unreachable:
            _assert_never(unreachable)


def _emit_verification_failure(
    phase_name: str,
    failure_reason: str | None,
    on_failure_route: str | None,
) -> list[Event]:
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
