from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

from loguru import logger

from ralph.mcp.artifacts.development_result import DevelopmentResult
from ralph.mcp.artifacts.md_draft_io import (
    md_draft_path,
    seeded_draft_workspace_path,
    unsubmitted_draft_divergence,
)
from ralph.mcp.artifacts.plan._section_registry import PLAN_ARTIFACT_PATH
from ralph.mcp.artifacts.plan._validation import is_noop_plan
from ralph.phases.artifacts import (
    PhaseArtifactError,
    artifact_validation_failure_event,
    legacy_json_rejection_detail,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    PREMISE_CHECK_LINE,
    build_missing_input_hint,
    build_required_artifacts,
    build_retry_hint,
    clear_validation_retry_hint,
    resolve_phase_required_artifact,
    retry_fingerprint_path_for_hint,
    retry_hint_fingerprint,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import (
    Event,
    ExecutionResultEvent,
    PhaseFailureEvent,
    PipelineEvent,
)
from ralph.recovery.retry_prompt import premise_check_line

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.policy.models import (
        ArtifactsPolicy,
        PhaseDefinition,
        PipelinePolicy,
    )
    from ralph.workspace import Workspace

from ralph.phases.artifact_freshness import (
    is_stale_since_invocation,
    stale_artifact_detail,
)
from ralph.workspace.probe import WorkspaceChangeProbe, take_workspace_snapshot


def _probe_from_context(ctx: PhaseContext) -> WorkspaceChangeProbe | None:
    probe_candidate: object = getattr(ctx, "workspace_probe", None)
    if isinstance(probe_candidate, WorkspaceChangeProbe):
        return probe_candidate
    return None


def _snapshot_file_path(phase: str) -> str:
    return f".agent/tmp/pre_execution_snapshot_{phase}.json"


def record_workspace_snapshot(workspace: Workspace, phase: str) -> dict[str, str]:
    snap = take_workspace_snapshot(workspace)
    snap_path = _snapshot_file_path(phase)
    with suppress(Exception):
        workspace.write(snap_path, json.dumps(snap))
    return snap


def _load_pre_execution_snapshot(workspace: Workspace, phase: str) -> dict[str, str] | None:
    snap_path = _snapshot_file_path(phase)
    try:
        if workspace.exists(snap_path):
            raw = workspace.read(snap_path)
            with suppress(Exception):
                workspace.remove(snap_path)
            parsed: object = json.loads(raw)
            if isinstance(parsed, dict):
                parsed_dict = cast("dict[object, object]", parsed)
                return {str(k): str(v) for k, v in parsed_dict.items()}
    except Exception:
        pass
    return None


def detect_workspace_files_changed(
    workspace: Workspace,
    phase: str,
    *,
    baseline: dict[str, str] | None = None,
) -> bool:
    after = take_workspace_snapshot(workspace)
    before = baseline if baseline is not None else _load_pre_execution_snapshot(workspace, phase)

    if before is not None:
        return before != after

    return False


def _detect_workspace_files_changed(workspace: Workspace, phase: str = "") -> bool:
    return detect_workspace_files_changed(workspace, phase)


def _handle_prepare_prompt(effect: PreparePromptEffect, ctx: PhaseContext) -> list[Event]:
    phase = effect.phase
    phase_def = ctx.pipeline_policy.phases.get(phase)
    drain = phase_def.drain if phase_def is not None else phase
    ra = resolve_phase_required_artifact(
        ctx.pipeline_policy, ctx.artifacts_policy, phase=phase, drain=drain
    )

    if ra is not None and ra.artifact_type == "plan":
        _clear_stale_plan_draft_if_needed(ctx)

    probe = _probe_from_context(ctx)
    if probe is not None:
        probe.observe_before(phase, ctx.workspace)
    else:
        record_workspace_snapshot(ctx.workspace, phase)

    logger.info("Execution phase '{}': preparing prompt", phase)
    return [PipelineEvent.PROMPT_PREPARED]


def _apply_workspace_changes(
    events: list[Event],
    ctx: PhaseContext,
    phase: str,
    pre_execution_snapshot: dict[str, str] | None,
) -> list[Event]:
    probe = _probe_from_context(ctx)
    if any(isinstance(evt, PhaseFailureEvent) for evt in events):
        if probe is not None:
            changed = probe.has_changed(phase, ctx.workspace)
        else:
            changed = detect_workspace_files_changed(
                ctx.workspace,
                phase,
                baseline=pre_execution_snapshot,
            )
        return [
            replace(evt, workspace_changed=changed) if isinstance(evt, PhaseFailureEvent) else evt
            for evt in events
        ]
    if probe is not None:
        probe.has_changed(phase, ctx.workspace)
    else:
        _load_pre_execution_snapshot(ctx.workspace, phase)
    return events


def handle_execution_phase(
    effect: Effect,
    ctx: PhaseContext,
    *,
    output_artifact_path: str | None = None,
    assigned_work_unit_id: str | None = None,
    pre_execution_snapshot: dict[str, str] | None = None,
) -> list[Event]:
    if isinstance(effect, PreparePromptEffect):
        return _handle_prepare_prompt(effect, ctx)

    if not isinstance(effect, InvokeAgentEffect):
        return []

    phase = effect.phase
    logger.info("Execution phase '{}': validating output artifact after agent run", phase)

    phase_def = ctx.pipeline_policy.phases.get(phase)
    drain = phase_def.drain if phase_def is not None else phase
    ra = resolve_phase_required_artifact(
        ctx.pipeline_policy, ctx.artifacts_policy, phase=phase, drain=drain
    )
    if ra is not None and output_artifact_path is not None:
        ra = replace(ra, artifact_path=output_artifact_path)
    worker_retry_hint_path = _worker_retry_hint_path(
        drain,
        output_artifact_path,
    )

    events: list[Event] | None = None
    if ra is not None and ra.artifact_type == "plan":
        events = _validate_plan_output(effect, ctx, ra, phase_def)
    elif ra is not None and ra.artifact_type == "development_result":
        plan_result = _validate_plan_input(
            effect,
            ctx,
            retry_hint_path_override=worker_retry_hint_path,
        )
        if plan_result is not None:
            events = plan_result if plan_result else [PipelineEvent.AGENT_SUCCESS]

    if events is None and ra is not None:
        failure, validated_content = _validate_output_artifact(
            effect,
            ctx,
            ra,
            retry_hint_path_override=worker_retry_hint_path,
        )
        if failure is not None:
            events = failure
        elif ra.artifact_type == "development_result" and validated_content is not None:
            development_result = DevelopmentResult.model_validate(validated_content)
            events = [
                ExecutionResultEvent(
                    phase=phase,
                    status=development_result.status,
                )
            ]

    if events is None:
        events = [PipelineEvent.AGENT_SUCCESS]

    events = _apply_workspace_changes(
        events,
        ctx,
        phase,
        pre_execution_snapshot,
    )
    return _clear_retry_hint_after_accepted_artifact(
        events,
        ctx,
        phase,
        ra,
        worker_retry_hint_path,
    )


def _clear_retry_hint_after_accepted_artifact(
    events: list[Event],
    ctx: PhaseContext,
    phase: str,
    required_artifact: RequiredArtifact | None,
    worker_retry_hint_path: str | None,
) -> list[Event]:
    if required_artifact is not None and not any(
        isinstance(event, PhaseFailureEvent) for event in events
    ):
        clear_validation_retry_hint(
            ctx.workspace,
            phase,
            pipeline_policy=ctx.pipeline_policy,
            hint_path_override=worker_retry_hint_path,
        )
    return events


def _clear_stale_plan_draft_if_needed(ctx: PhaseContext) -> None:
    draft_path = md_draft_path(Path(".agent/artifacts"), "plan").as_posix()
    if not ctx.workspace.exists(draft_path):
        return
    if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
        return
    seeded_path = seeded_draft_workspace_path("plan")
    if ctx.workspace.read(draft_path) == ctx.workspace.read(
        PLAN_ARTIFACT_PATH
    ) and not ctx.workspace.exists(seeded_path):
        logger.info("Clearing submitted plan draft at {}", draft_path)
        ctx.workspace.remove(draft_path)
        if ctx.workspace.exists(seeded_path):
            ctx.workspace.remove(seeded_path)


def _validate_plan_output(
    effect: InvokeAgentEffect,
    ctx: PhaseContext,
    ra: RequiredArtifact,
    phase_def: PhaseDefinition | None,
) -> list[Event]:
    phase = effect.phase
    if not ctx.workspace.exists(ra.artifact_path):
        detail = legacy_json_rejection_detail(ctx.workspace, ra.artifact_path) or (
            f"Missing required plan artifact at {ra.artifact_path}; "
            "the agent must submit plan before declaring completion"
        )
        logger.warning("Planning agent completed without producing {}", ra.artifact_path)
        _write_retry_hint(ctx, phase, detail)
        return [artifact_validation_failure_event(phase=phase, reason=detail)]
    if is_stale_since_invocation(ctx.workspace, phase, ra.artifact_path):
        detail = stale_artifact_detail(ra.artifact_path, ra.artifact_type)
        logger.warning("Planning agent completed without updating {}", ra.artifact_path)
        _write_retry_hint(ctx, phase, detail)
        return [artifact_validation_failure_event(phase=phase, reason=detail)]
    artifact_path = Path(ctx.workspace.absolute_path(ra.artifact_path))
    divergence = unsubmitted_draft_divergence(
        artifact_path.parent,
        ra.artifact_type,
        artifact_path,
    )
    if divergence is not None:
        detail = "The staged plan draft contains content that was never submitted."
        _write_retry_hint(
            ctx,
            phase,
            detail,
            unsubmitted_draft=True,
            preserve_existing=True,
        )
        return [artifact_validation_failure_event(phase=phase, reason=detail)]
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, ra.artifact_path)
        raw_content = unwrap_phase_artifact_content(
            artifact_wrapper, expected_type=ra.artifact_type
        )
        if is_noop_plan(raw_content):
            logger.info("Planning produced a no-op plan — skipping development iteration")
            return [PipelineEvent.AGENT_SUCCESS]
    except ValueError as exc:
        logger.warning("Invalid plan artifact: {}", exc)
        _write_retry_hint(ctx, phase, str(exc))
        return [
            artifact_validation_failure_event(
                phase=phase,
                reason=f"Invalid plan artifact: {exc}",
            )
        ]
    return [PipelineEvent.AGENT_SUCCESS]


def _validate_plan_input(
    effect: InvokeAgentEffect,
    ctx: PhaseContext,
    *,
    retry_hint_path_override: str | None = None,
) -> list[Event] | None:
    phase = effect.phase
    if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
        upstream = _find_plan_producing_phase(ctx.pipeline_policy, ctx.artifacts_policy)
        detail = f"Missing planning artifact at {PLAN_ARTIFACT_PATH}"
        hint = build_missing_input_hint(phase, upstream, PLAN_ARTIFACT_PATH)
        with suppress(Exception):
            ctx.workspace.write(
                retry_hint_path_override
                or retry_hint_path(phase, pipeline_policy=ctx.pipeline_policy),
                hint,
            )
        return [artifact_validation_failure_event(phase=phase, reason=detail)]
    try:
        artifact_wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
        artifact_content = unwrap_phase_artifact_content(artifact_wrapper, expected_type="plan")
        if is_noop_plan(artifact_content):
            return []
    except (PhaseArtifactError, ValueError) as exc:
        logger.warning("Invalid development phase evidence: {}", exc)
        _write_retry_hint(
            ctx,
            phase,
            str(exc),
            hint_path_override=retry_hint_path_override,
        )
        return [
            artifact_validation_failure_event(
                phase=phase,
                reason=f"Invalid development evidence: {exc}",
            )
        ]
    return None


def _validate_output_artifact(
    effect: InvokeAgentEffect,
    ctx: PhaseContext,
    ra: RequiredArtifact,
    *,
    retry_hint_path_override: str | None = None,
) -> tuple[list[Event] | None, dict[str, object] | None]:
    phase = effect.phase
    if not ctx.workspace.exists(ra.artifact_path):
        if not ra.artifact_required:
            logger.debug(
                "Execution phase '{}': optional artifact at {} absent — treating as success",
                phase,
                ra.artifact_path,
            )
            return None, None
        detail = legacy_json_rejection_detail(ctx.workspace, ra.artifact_path) or (
            f"Missing required artifact at {ra.artifact_path}; "
            f"the agent must submit {ra.artifact_type} before declaring completion"
        )
        logger.warning(
            "Execution phase '{}' missing required artifact at {}", phase, ra.artifact_path
        )
        _write_retry_hint(
            ctx,
            phase,
            detail,
            hint_path_override=retry_hint_path_override,
        )
        return [artifact_validation_failure_event(phase=phase, reason=detail)], None

    if ra.artifact_required and is_stale_since_invocation(ctx.workspace, phase, ra.artifact_path):
        detail = stale_artifact_detail(ra.artifact_path, ra.artifact_type)
        logger.warning(
            "Execution phase '{}' did not update required artifact at {}", phase, ra.artifact_path
        )
        _write_retry_hint(ctx, phase, detail, hint_path_override=retry_hint_path_override)
        return [artifact_validation_failure_event(phase=phase, reason=detail)], None

    try:
        artifact = load_phase_artifact(
            ctx.workspace,
            ra.artifact_path,
            artifact_type=ra.artifact_type,
        )
        content = unwrap_phase_artifact_content(
            artifact,
            expected_type=ra.artifact_type,
        )
        normalized = ra.normalizer(content) if ra.normalizer is not None else content
    except PhaseArtifactError as exc:
        invalid_detail = str(exc)
    except ValueError as exc:
        invalid_detail = f"Artifact at {ra.artifact_path} failed validation: {exc}"
    else:
        return None, normalized

    if invalid_detail:
        logger.warning(
            "Invalid {} artifact in execution phase '{}': {}",
            ra.artifact_type,
            phase,
            invalid_detail,
        )
        _write_retry_hint(
            ctx,
            phase,
            invalid_detail,
            hint_path_override=retry_hint_path_override,
        )
    return [
        artifact_validation_failure_event(
            phase=phase,
            reason=f"Invalid {ra.artifact_type} artifact: {invalid_detail}",
        )
    ], None


class PersistedRetryHint(NamedTuple):
    hint: str
    fingerprint: str


def _strip_premise_check_line(text: str) -> str:
    kept = [line for line in text.splitlines() if line.strip() != PREMISE_CHECK_LINE]
    return "\n".join(kept).strip()


def compose_persisted_retry_hint(
    phase: str,
    detail: str,
    *,
    prior_hint: str = "",
    prior_fingerprint: str = "",
    registry: dict[str, RequiredArtifact] | None = None,
    unsubmitted_draft: bool = False,
) -> PersistedRetryHint:
    fingerprint = retry_hint_fingerprint(phase, detail, registry=registry)
    block = build_retry_hint(
        phase,
        detail,
        registry=registry,
        unsubmitted_draft=unsubmitted_draft,
        validation=True,
    )
    history = _strip_premise_check_line(prior_hint)
    hint = f"{history}\n\n{block}" if history else block
    premise = premise_check_line(fingerprint, prior_fingerprint or None)
    if premise:
        hint = f"{hint}\n\n{premise}"
    return PersistedRetryHint(hint=hint, fingerprint=fingerprint)


def _write_retry_hint(
    ctx: PhaseContext,
    phase: str,
    detail: str,
    *,
    hint_path_override: str | None = None,
    unsubmitted_draft: bool = False,
    preserve_existing: bool = True,
) -> None:
    hint_path = hint_path_override or retry_hint_path(phase, pipeline_policy=ctx.pipeline_policy)
    fingerprint_path = retry_fingerprint_path_for_hint(hint_path)
    try:
        registry = build_required_artifacts(ctx.artifacts_policy)
    except AttributeError:
        registry = None
    prior_fingerprint = ""
    prior_hint = ""
    with suppress(Exception):
        if ctx.workspace.exists(fingerprint_path):
            prior_fingerprint = ctx.workspace.read(fingerprint_path).strip()
    with suppress(Exception):
        if preserve_existing and ctx.workspace.exists(hint_path):
            prior_hint = ctx.workspace.read(hint_path)
    persisted = compose_persisted_retry_hint(
        phase,
        detail,
        prior_hint=prior_hint,
        prior_fingerprint=prior_fingerprint,
        registry=registry,
        unsubmitted_draft=unsubmitted_draft,
    )
    with suppress(Exception):
        ctx.workspace.write(hint_path, persisted.hint)
        ctx.workspace.write(fingerprint_path, persisted.fingerprint)


def _worker_retry_hint_path(
    drain: str,
    output_artifact_path: str | None,
) -> str | None:
    if output_artifact_path is None:
        return None
    artifact_path = Path(output_artifact_path)
    if artifact_path.parent.name != "artifacts":
        return None
    return str(artifact_path.parent.parent / "tmp" / f"last_retry_error_{drain}.txt")


def _find_plan_producing_phase(
    pipeline_policy: PipelinePolicy, artifacts_policy: ArtifactsPolicy
) -> str:
    for contract in artifacts_policy.artifacts.values():
        if contract.artifact_type == "plan":
            for phase_name, phase_def in pipeline_policy.phases.items():
                if phase_def.drain == contract.drain:
                    return phase_name
    return pipeline_policy.entry_phase
