
from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.plan._section_registry import PLAN_ARTIFACT_PATH
from ralph.mcp.artifacts.plan._validation import is_noop_plan
from ralph.phases.artifact_freshness import (
    is_stale_since_invocation,
    stale_artifact_detail,
)
from ralph.phases.artifacts import (
    PhaseArtifactError,
    artifact_validation_failure_event,
    decision_vocabulary_for_drain,
    legacy_json_rejection_detail,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    build_required_artifacts,
    build_retry_hint,
    clear_validation_retry_hint,
    resolve_required_artifact,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import AnalysisDecisionEvent, Event, PipelineEvent

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
    from ralph.phases.required_artifacts import RequiredArtifact


def parse_analysis_decision_status(
    ctx: PhaseContext,
    drain_name: str,
    *,
    phase_name: str | None = None,
) -> str | None:
    policy_phase_name = phase_name if phase_name is not None else drain_name
    artifact_type = f"{drain_name}_decision"
    ra = resolve_required_artifact(ctx.artifacts_policy, drain=drain_name)
    artifact_path = ra.artifact_path if ra is not None else f".agent/artifacts/{artifact_type}.md"

    if not ctx.workspace.exists(artifact_path):
        legacy_detail = legacy_json_rejection_detail(ctx.workspace, artifact_path)
        if legacy_detail is not None:
            logger.warning(legacy_detail)
            return None
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
    if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
        return False
    with suppress(
        PhaseArtifactError,
        ValueError,
        Exception,
    ):
        wrapper = load_phase_artifact(ctx.workspace, PLAN_ARTIFACT_PATH)
        raw = unwrap_phase_artifact_content(wrapper, expected_type="plan")
        return is_noop_plan(raw)
    return False


def _missing_or_stale_detail(
    ctx: PhaseContext, phase_name: str, drain_name: str, artifact_path: str
) -> str | None:
    if not ctx.workspace.exists(artifact_path):
        logger.warning(
            "Analysis phase '{}' completed without required artifact at {}",
            phase_name,
            artifact_path,
        )
        return legacy_json_rejection_detail(ctx.workspace, artifact_path) or (
            f"Missing required analysis artifact at {artifact_path}; "
            f"the agent must submit {drain_name}_decision before declaring completion"
        )
    if is_stale_since_invocation(ctx.workspace, phase_name, artifact_path):
        logger.warning(
            "Analysis phase '{}' did not update required artifact at {}",
            phase_name,
            artifact_path,
        )
        return stale_artifact_detail(artifact_path, f"{drain_name}_decision")
    return None


def _write_analysis_retry_hint(
    ctx: PhaseContext, phase_name: str, detail: str, registry: dict[str, RequiredArtifact] | None
) -> None:
    with suppress(Exception):
        hint_path = retry_hint_path(phase_name, pipeline_policy=ctx.pipeline_policy)
        hint = build_retry_hint(phase_name, detail, registry=registry)
        if ctx.workspace.exists(hint_path):
            existing = ctx.workspace.read(hint_path).strip()
            if existing:
                hint = f"{existing}\n\n{hint}"
        ctx.workspace.write(hint_path, hint)


def handle_generic_analysis_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
    if isinstance(effect, PreparePromptEffect):
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        phase_name = str(effect.phase)
        drain_name = effect.drain if effect.drain is not None else phase_name

        if _has_noop_plan(ctx):
            logger.info("Analysis phase '{}': plan is a no-op — skipping analysis", phase_name)
            return [AnalysisDecisionEvent(phase=phase_name, decision="completed")]

        try:
            registry = build_required_artifacts(ctx.artifacts_policy)
        except AttributeError:
            registry = None
        ra = resolve_required_artifact(ctx.artifacts_policy, drain=drain_name)
        artifact_path = (
            ra.artifact_path if ra is not None else f".agent/artifacts/{drain_name}_decision.md"
        )

        detail = _missing_or_stale_detail(ctx, phase_name, drain_name, artifact_path)
        if detail is not None:
            _write_analysis_retry_hint(ctx, phase_name, detail, registry)
            return [artifact_validation_failure_event(phase=phase_name, reason=detail)]

        status = parse_analysis_decision_status(ctx, drain_name, phase_name=phase_name)
        if status is None:
            return [
                artifact_validation_failure_event(
                    phase=phase_name,
                    reason=f"Unroutable analysis decision for phase '{phase_name}'",
                )
            ]

        logger.info("Analysis phase '{}' decision: {}", phase_name, status)
        clear_validation_retry_hint(
            ctx.workspace,
            phase_name,
            pipeline_policy=ctx.pipeline_policy,
        )
        return [AnalysisDecisionEvent(phase=phase_name, decision=status)]

    return []
