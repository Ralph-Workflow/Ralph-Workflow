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
import re
from contextlib import suppress
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.development_result import DevelopmentResult
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
    artifact_validation_failure_event,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    build_missing_input_hint,
    build_proof_failure_hint,
    build_retry_hint,
    resolve_phase_required_artifact,
    resolve_required_artifact,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.work_units import WorkUnitsValidationError, parse_work_units_from_artifact
from ralph.policy.validation import PolicyValidationError, validate_work_units_against_policy

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.policy.models import (
        ArtifactProofPolicy,
        ArtifactsPolicy,
        PhaseDefinition,
        PipelinePolicy,
    )


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
        ra = resolve_phase_required_artifact(
            ctx.pipeline_policy, ctx.artifacts_policy, phase=phase, drain=drain
        )

        if ra is not None and ra.artifact_type == "plan":
            _clear_stale_plan_draft_if_needed(ctx)

        logger.info("Execution phase '{}': preparing prompt", phase)
        return [PipelineEvent.PROMPT_PREPARED]

    if not isinstance(effect, InvokeAgentEffect):
        return []

    phase = effect.phase
    logger.info("Execution phase '{}': validating output artifact after agent run", phase)

    phase_def = ctx.pipeline_policy.phases.get(phase)
    drain = phase_def.drain if phase_def is not None else phase
    ra = resolve_phase_required_artifact(
        ctx.pipeline_policy, ctx.artifacts_policy, phase=phase, drain=drain
    )

    events: list[Event] | None = None
    if ra is not None and ra.artifact_type == "plan":
        events = _validate_plan_output(effect, ctx, ra, phase_def)
    elif ra is not None and ra.artifact_type == "development_result":
        plan_result = _validate_plan_input(effect, ctx)
        if plan_result is not None:
            events = plan_result if plan_result else [PipelineEvent.AGENT_SUCCESS]

    if events is None and ra is not None:
        failure = _validate_output_artifact(effect, ctx, ra)
        if failure is not None:
            events = failure
        elif (
            ra.artifact_type == "development_result"
            and phase_def is not None
            and phase_def.artifact_proof_policy is not None
        ):
            proof_failure = _validate_development_result_proof(
                ctx, phase, phase_def.artifact_proof_policy, ra
            )
            if proof_failure is not None:
                events = proof_failure

    if events is None:
        events = [PipelineEvent.AGENT_SUCCESS]
    return events


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
        return [artifact_validation_failure_event(phase=phase, reason=detail)]
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
            artifact_validation_failure_event(
                phase=phase,
                reason=f"Invalid plan artifact: {exc}",
            )
        ]
    return [PipelineEvent.AGENT_SUCCESS]


def _validate_plan_input(effect: InvokeAgentEffect, ctx: PhaseContext) -> list[Event] | None:
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
        return [artifact_validation_failure_event(phase=phase, reason=detail)]
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
        artifact_content = unwrap_phase_artifact_content(artifact_wrapper, expected_type="plan")
        if is_noop_plan(artifact_content):
            return []
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
            artifact_validation_failure_event(
                phase=phase,
                reason=f"Invalid development evidence: {exc}",
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
        logger.warning("Execution phase '{}' missing required artifact at {}", phase, ra.json_path)
        _write_retry_hint(ctx, phase, detail)
        return [artifact_validation_failure_event(phase=phase, reason=detail)]
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, ra.json_path)
        content = unwrap_phase_artifact_content(artifact_wrapper, expected_type=ra.artifact_type)
        if ra.normalizer is not None:
            ra.normalizer(content)
    except (json.JSONDecodeError, PhaseArtifactError, ValueError) as exc:
        detail = str(exc)
        logger.warning(
            "Invalid {} artifact in execution phase '{}': {}", ra.artifact_type, phase, detail
        )
        _write_retry_hint(ctx, phase, detail)
        return [
            artifact_validation_failure_event(
                phase=phase,
                reason=f"Invalid {ra.artifact_type} artifact: {detail}",
            )
        ]
    return None


def _write_retry_hint(ctx: PhaseContext, phase: str, detail: str) -> None:
    hint_path = retry_hint_path(phase)
    hint = build_retry_hint(phase, detail)
    with suppress(Exception):
        ctx.workspace.write(hint_path, hint)


def _write_proof_failure_hint(ctx: PhaseContext, phase: str, detail: str) -> None:
    hint_path = retry_hint_path(phase)
    hint = build_proof_failure_hint(phase, detail)
    with suppress(Exception):
        ctx.workspace.write(hint_path, hint)


def _step_proof_errors(required_refs: frozenset[str], submitted_list: list[str]) -> list[str]:
    errors: list[str] = []
    submitted_set = frozenset(submitted_list)
    if len(submitted_set) < len(submitted_list):
        errors.append("PROOF INVALID: Duplicate plan_item entries found in plan_items_proven.")
    missing = required_refs - submitted_set
    extra = submitted_set - required_refs
    if missing:
        errors.append(
            "PROOF INCOMPLETE: The following plan step(s) have no proof entry: "
            f'{sorted(missing)}. Each plan_item must exactly match "Step N: <title>".'
        )
    if extra:
        errors.append(
            "PROOF INVALID: Unknown plan_item reference(s) not matching any plan step: "
            f"{sorted(extra)}."
        )
    return errors


def _work_unit_proof_errors(required_refs: frozenset[str], submitted_list: list[str]) -> list[str]:
    errors: list[str] = []
    submitted_set = frozenset(submitted_list)
    if len(submitted_set) < len(submitted_list):
        errors.append("PROOF INVALID: Duplicate plan_item entries found in plan_items_proven.")
    if not submitted_set:
        errors.append(
            "PROOF INCOMPLETE: plan_items_proven is empty. The agent must prove at least "
            "one work unit. Each plan_item must exactly match a work_unit unit_id from the plan."
        )
    extra = submitted_set - required_refs
    if extra:
        errors.append(
            "PROOF INVALID: Unknown plan_item reference(s) not matching any work_unit unit_id: "
            f"{sorted(extra)}. Valid unit_ids: {sorted(required_refs)}."
        )
    return errors


def _normalize_analysis_proof_ref(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", value.casefold())).strip()



def _match_analysis_proof_ref(required_refs: frozenset[str], submitted_ref: str) -> str | None:
    normalized_submitted = _normalize_analysis_proof_ref(submitted_ref)
    if not normalized_submitted:
        return None

    exact_matches = [
        ref for ref in required_refs if _normalize_analysis_proof_ref(ref) == normalized_submitted
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None

    scored_matches = sorted(
        (
            (
                SequenceMatcher(
                    None,
                    normalized_submitted,
                    _normalize_analysis_proof_ref(required_ref),
                ).ratio(),
                required_ref,
            )
            for required_ref in required_refs
        ),
        reverse=True,
    )
    if not scored_matches:
        return None

    best_score, best_match = scored_matches[0]
    second_best_score = scored_matches[1][0] if len(scored_matches) > 1 else 0.0
    if best_score < 0.88 or best_score - second_best_score < 0.03:
        return None
    return best_match



def _analysis_proof_errors(required_refs: frozenset[str], submitted_list: list[str]) -> list[str]:
    errors: list[str] = []
    matched_refs: list[str] = []
    unmatched_refs: list[str] = []

    for submitted_ref in submitted_list:
        matched_ref = _match_analysis_proof_ref(required_refs, submitted_ref)
        if matched_ref is None:
            unmatched_refs.append(submitted_ref)
            continue
        matched_refs.append(matched_ref)

    if len(set(matched_refs)) < len(matched_refs):
        errors.append(
            "PROOF INVALID: Duplicate how_to_fix_item entries found in analysis_items_addressed."
        )
    missing = required_refs - frozenset(matched_refs)
    if missing:
        errors.append(
            "PROOF INCOMPLETE: The following how_to_fix item(s) have no proof entry: "
            f"{sorted(missing)}. Each how_to_fix_item must exactly match the prior analysis text."
        )
    if unmatched_refs:
        errors.append(
            "PROOF INVALID: Unknown how_to_fix_item reference(s) not matching any prior "
            f"analysis item: {sorted(frozenset(unmatched_refs))}."
        )
    return errors


def _plan_proof_errors(ctx: PhaseContext, dev_result: DevelopmentResult) -> list[str]:
    step_refs = _get_canonical_step_refs(ctx)
    if step_refs:
        return _step_proof_errors(step_refs, [p.plan_item for p in dev_result.plan_items_proven])
    work_unit_ids = _get_canonical_work_unit_ids(ctx)
    if work_unit_ids:
        return _work_unit_proof_errors(
            work_unit_ids, [p.plan_item for p in dev_result.plan_items_proven]
        )
    return []


def _get_canonical_step_refs(ctx: PhaseContext) -> frozenset[str]:
    refs: set[str] = set()
    try:
        if ctx.workspace.exists(PLAN_ARTIFACT_PATH):
            artifact_wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
            content = unwrap_phase_artifact_content(artifact_wrapper, expected_type="plan")
            if not is_noop_plan(content):
                steps = content.get("steps")
                if isinstance(steps, list) and steps:
                    for step in steps:
                        if not isinstance(step, dict):
                            return frozenset()
                        if "number" not in step or "title" not in step:
                            return frozenset()
                        refs.add(f"Step {step['number']}: {step['title']}")
    except Exception:
        return frozenset()
    return frozenset(refs)


def _get_canonical_work_unit_ids(ctx: PhaseContext) -> frozenset[str]:
    try:
        if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
            return frozenset()
        artifact_wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
        content = unwrap_phase_artifact_content(artifact_wrapper, expected_type="plan")
        if is_noop_plan(content):
            return frozenset()
        parsed = parse_work_units_from_artifact(content)
        if parsed is None or not parsed.work_units:
            return frozenset()
        return frozenset(unit.unit_id for unit in parsed.work_units)
    except Exception:
        return frozenset()


def _get_canonical_analysis_how_to_fix_refs(ctx: PhaseContext, phase: str) -> frozenset[str]:
    try:
        for phase_def in ctx.pipeline_policy.phases.values():
            if phase_def.role != "analysis" or phase_def.transitions.on_loopback != phase:
                continue
            ra = resolve_required_artifact(ctx.artifacts_policy, drain=phase_def.drain)
            if ra is None or not ctx.workspace.exists(ra.json_path):
                return frozenset()
            artifact_wrapper = load_phase_artifact(ctx.workspace, ra.json_path)
            content = unwrap_phase_artifact_content(
                artifact_wrapper, expected_type=ra.artifact_type
            )
            how_to_fix = content.get("how_to_fix")
            if not isinstance(how_to_fix, list):
                return frozenset()
            return frozenset(str(item) for item in how_to_fix if item)
        return frozenset()
    except Exception:
        return frozenset()


def _validate_development_result_proof(
    ctx: PhaseContext,
    phase: str,
    proof_policy: ArtifactProofPolicy,
    ra: RequiredArtifact,
) -> list[Event] | None:
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, ra.json_path)
        raw_content = unwrap_phase_artifact_content(
            artifact_wrapper, expected_type=ra.artifact_type
        )
        dev_result = DevelopmentResult.model_validate(raw_content)
    except Exception:
        return None

    errors: list[str] = []
    if proof_policy.require_plan_proof:
        errors.extend(_plan_proof_errors(ctx, dev_result))
    if proof_policy.require_analysis_proof:
        required_refs = _get_canonical_analysis_how_to_fix_refs(ctx, phase)
        if required_refs:
            errors.extend(
                _analysis_proof_errors(
                    required_refs,
                    [a.how_to_fix_item for a in dev_result.analysis_items_addressed],
                )
            )

    if not errors:
        return None

    detail = "\n".join(errors)
    _write_proof_failure_hint(ctx, phase, detail)
    return [artifact_validation_failure_event(phase=phase, reason=detail)]


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
