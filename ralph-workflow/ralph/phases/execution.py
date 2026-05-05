"""Generic execution phase handler.

Handles any phase with role='execution'. Behavior is determined by the drain's
artifact contract:

- Drain produces artifact_type='plan': plan validation, noop detection, plan draft
  management (PreparePromptEffect clears stale drafts).
- Drain produces artifact_type='development_result': plan INPUT validation (noop
  short-circuit + work-unit policy check) before validating the output artifact.
- All other drains: validate the configured output artifact contract only.

On PreparePromptEffect: clears a stale plan draft when the phase produces a plan.
On InvokeAgentEffect: validates the output artifact contract, with type-specific
pre-validation for plan and development_result drains.
"""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.plan import (
    PLAN_ARTIFACT_PATH,
    PLAN_DRAFT_PATH,
    PlanArtifactValidationError,
    is_noop_plan,
    load_plan_draft,
    normalize_plan_artifact_content,
)
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    build_missing_input_hint,
    build_retry_hint,
    resolve_required_artifact,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.work_units import WorkUnitsValidationError, parse_work_units_from_artifact
from ralph.policy.validation import PolicyValidationError, validate_work_units_against_policy

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.policy.models import ArtifactsPolicy, PhaseDefinition, PipelinePolicy


def handle_execution_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Generic handler for any phase with role='execution'.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        phase = effect.phase
        phase_def = ctx.pipeline_policy.phases.get(phase)
        drain = phase_def.drain if phase_def is not None else phase
        ra = resolve_required_artifact(ctx.artifacts_policy, drain=drain)

        if ra is not None and ra.artifact_type == "plan":
            _clear_stale_plan_draft_if_needed(ctx)

        logger.info("Execution phase '{}': preparing prompt", phase)
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        phase = effect.phase
        logger.info("Execution phase '{}': validating output artifact after agent run", phase)

        phase_def = ctx.pipeline_policy.phases.get(phase)
        drain = phase_def.drain if phase_def is not None else phase
        ra = resolve_required_artifact(ctx.artifacts_policy, drain=drain)

        if ra is not None and ra.artifact_type == "plan":
            return _validate_plan_output(effect, ctx, ra, phase_def)

        if ra is not None and ra.artifact_type == "development_result":
            plan_result = _validate_plan_input(effect, ctx)
            if plan_result is not None:
                return plan_result if plan_result else [PipelineEvent.AGENT_SUCCESS]

        if ra is not None:
            failure = _validate_output_artifact(effect, ctx, ra)
            if failure is not None:
                return failure

        return [PipelineEvent.AGENT_SUCCESS]

    return []


def _clear_stale_plan_draft_if_needed(ctx: PhaseContext) -> None:
    if not ctx.workspace.exists(PLAN_DRAFT_PATH):
        return
    if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
        return
    artifact_dir = Path(ctx.workspace.absolute_path(".agent/artifacts"))
    draft = load_plan_draft(artifact_dir)
    if draft is None:
        return
    updated_at = draft.get("updated_at")
    if not isinstance(updated_at, str):
        return
    try:
        draft_updated_at = datetime.fromisoformat(updated_at).timestamp()
    except ValueError:
        return
    plan_mtime = Path(ctx.workspace.absolute_path(PLAN_ARTIFACT_PATH)).stat().st_mtime
    if draft_updated_at <= plan_mtime:
        logger.info("Clearing stale plan draft at {}", PLAN_DRAFT_PATH)
        ctx.workspace.remove(PLAN_DRAFT_PATH)


def _validate_plan_output(
    effect: InvokeAgentEffect,
    ctx: PhaseContext,
    ra: RequiredArtifact,
    phase_def: PhaseDefinition | None,
) -> list[Event]:
    """Validate the plan artifact produced by a planning-type phase."""
    phase = effect.phase
    if not ctx.workspace.exists(ra.json_path):
        detail = (
            f"Missing required plan artifact at {ra.json_path}; "
            "the agent must submit plan before declaring completion"
        )
        logger.warning("Planning agent completed without producing {}", ra.json_path)
        _write_retry_hint(ctx, phase, detail)
        return [
            PhaseFailureEvent(phase=phase, reason=detail, recoverable=True, retry_in_session=True)
        ]
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, ra.json_path)
        raw_content = unwrap_phase_artifact_content(
            artifact_wrapper, expected_type=ra.artifact_type
        )
        if is_noop_plan(raw_content):
            logger.info("Planning produced a no-op plan — skipping development iteration")
            return [PipelineEvent.AGENT_SUCCESS]
        artifact = normalize_plan_artifact_content(raw_content)
        parsed = parse_work_units_from_artifact(artifact)
        if parsed is not None:
            successor = _transitions_on_success(phase_def)
            validate_work_units_against_policy(
                parsed, ctx.pipeline_policy, phase=successor or phase
            )
    except (
        json.JSONDecodeError,
        PlanArtifactValidationError,
        ValueError,
        WorkUnitsValidationError,
        PolicyValidationError,
    ) as exc:
        logger.warning("Invalid plan artifact: {}", exc)
        _write_retry_hint(ctx, phase, str(exc))
        return [
            PhaseFailureEvent(
                phase=phase,
                reason=f"Invalid plan artifact: {exc}",
                recoverable=True,
                retry_in_session=True,
            )
        ]
    return [PipelineEvent.AGENT_SUCCESS]


def _validate_plan_input(
    effect: InvokeAgentEffect, ctx: PhaseContext
) -> list[Event] | None:
    """Validate the plan INPUT for development-type phases.

    Returns a list of failure events on error, an empty list to signal noop
    (caller should return AGENT_SUCCESS immediately), or None on success.
    """
    phase = effect.phase
    if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
        upstream = _find_plan_producing_phase(ctx.pipeline_policy, ctx.artifacts_policy)
        detail = f"Missing planning artifact at {PLAN_ARTIFACT_PATH}"
        hint = build_missing_input_hint(phase, upstream, PLAN_ARTIFACT_PATH)
        with suppress(Exception):
            ctx.workspace.write(retry_hint_path(phase), hint)
        return [
            PhaseFailureEvent(phase=phase, reason=detail, recoverable=True, retry_in_session=True)
        ]
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
        artifact_content = unwrap_phase_artifact_content(artifact_wrapper, expected_type="plan")
        if is_noop_plan(artifact_content):
            return []  # empty list = noop signal
        artifact = (
            artifact_content
            if _is_legacy_work_units_payload(artifact_content)
            else normalize_plan_artifact_content(artifact_content)
        )
        parsed = parse_work_units_from_artifact(artifact)
        if parsed is not None:
            validate_work_units_against_policy(parsed, ctx.pipeline_policy, phase=phase)
    except (
        json.JSONDecodeError,
        PlanArtifactValidationError,
        PhaseArtifactError,
        ValueError,
        WorkUnitsValidationError,
        PolicyValidationError,
    ) as exc:
        logger.warning("Invalid development phase evidence: {}", exc)
        _write_retry_hint(ctx, phase, str(exc))
        return [
            PhaseFailureEvent(
                phase=phase,
                reason=f"Invalid development evidence: {exc}",
                recoverable=True,
                retry_in_session=True,
            )
        ]
    return None


def _validate_output_artifact(
    effect: InvokeAgentEffect, ctx: PhaseContext, ra: RequiredArtifact
) -> list[Event] | None:
    """Validate the output artifact contract. Returns failure events if invalid, else None.

    When ra.artifact_required is False and the artifact is absent, returns None
    (treat as success). A present optional artifact is still parsed and type-checked.
    """
    phase = effect.phase
    if not ctx.workspace.exists(ra.json_path):
        if not ra.artifact_required:
            logger.debug(
                "Execution phase '{}': optional artifact at {} absent — treating as success",
                phase,
                ra.json_path,
            )
            return None
        detail = (
            f"Missing required artifact at {ra.json_path}; "
            f"the agent must submit {ra.artifact_type} before declaring completion"
        )
        logger.warning(
            "Execution phase '{}' missing required artifact at {}", phase, ra.json_path
        )
        _write_retry_hint(ctx, phase, detail)
        return [
            PhaseFailureEvent(
                phase=phase, reason=detail, recoverable=True, retry_in_session=True
            )
        ]
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, ra.json_path)
        unwrap_phase_artifact_content(artifact_wrapper, expected_type=ra.artifact_type)
    except (json.JSONDecodeError, PhaseArtifactError, ValueError) as exc:
        detail = str(exc)
        logger.warning(
            "Invalid {} artifact in execution phase '{}': {}", ra.artifact_type, phase, detail
        )
        _write_retry_hint(ctx, phase, detail)
        return [
            PhaseFailureEvent(
                phase=phase,
                reason=f"Invalid {ra.artifact_type} artifact: {detail}",
                recoverable=True,
                retry_in_session=True,
            )
        ]
    return None


def _write_retry_hint(ctx: PhaseContext, phase: str, detail: str) -> None:
    hint_path = retry_hint_path(phase)
    hint = build_retry_hint(phase, detail)
    with suppress(Exception):
        ctx.workspace.write(hint_path, hint)


def _is_legacy_work_units_payload(content: dict[str, object]) -> bool:
    return "work_units" in content and "summary" not in content


def _transitions_on_success(phase_def: PhaseDefinition | None) -> str | None:
    if phase_def is None:
        return None
    return phase_def.transitions.on_success


def _find_plan_producing_phase(
    pipeline_policy: PipelinePolicy, artifacts_policy: ArtifactsPolicy
) -> str:
    """Find the phase name that produces plan artifacts."""
    for contract in artifacts_policy.artifacts.values():
        if contract.artifact_type == "plan":
            for phase_name, phase_def in pipeline_policy.phases.items():
                if phase_def.drain == contract.drain:
                    return phase_name
    return pipeline_policy.entry_phase
