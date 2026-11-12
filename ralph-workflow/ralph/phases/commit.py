from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError
from loguru import logger

from ralph.git.errors import GitOperationError
from ralph.git.operations import has_uncommitted_changes
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    COMMIT_MESSAGE_TYPE,
    render_commit_message_content,
)
from ralph.phases.artifacts import (
    artifact_validation_failure_event,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    build_retry_hint,
    clear_validation_retry_hint,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
    from ralph.pipeline.events import Event


def _write_retry_hint(ctx: PhaseContext, phase: str, detail: str) -> None:
    hint_path = retry_hint_path(phase, pipeline_policy=ctx.pipeline_policy)
    hint = build_retry_hint(phase, detail, validation=True)
    with suppress(Exception):
        if ctx.workspace.exists(hint_path):
            existing = ctx.workspace.read(hint_path).strip()
            if existing:
                hint = f"{existing}\n\n{hint}"
        ctx.workspace.write(hint_path, hint)


def _has_no_diff(ctx: PhaseContext) -> bool:
    try:
        root = ctx.workspace.absolute_path(".")
        return not has_uncommitted_changes(root)
    except (
        AttributeError,
        TypeError,
        ValueError,
        OSError,
        InvalidGitRepositoryError,
        GitOperationError,
    ):
        return False


def _read_commit_message(ctx: PhaseContext) -> str | None:
    try:
        artifact = load_phase_artifact(
            ctx.workspace,
            COMMIT_MESSAGE_ARTIFACT,
            artifact_type=COMMIT_MESSAGE_TYPE,
        )
        content = unwrap_phase_artifact_content(artifact, expected_type=COMMIT_MESSAGE_TYPE)
        return render_commit_message_content(content)
    except Exception:
        return None


def handle_commit_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
    if not isinstance(effect, InvokeAgentEffect):
        return []

    phase_name = effect.phase

    if _has_no_diff(ctx):
        clear_validation_retry_hint(
            ctx.workspace,
            phase_name,
            pipeline_policy=ctx.pipeline_policy,
        )
        logger.info("{}: no diff to commit — skipping", phase_name)
        return [PipelineEvent.COMMIT_SKIPPED]

    if not ctx.workspace.exists(COMMIT_MESSAGE_ARTIFACT):
        logger.warning(
            "{} agent completed without producing {}",
            phase_name,
            COMMIT_MESSAGE_ARTIFACT,
        )
        detail = (
            f"Missing commit_message artifact at {COMMIT_MESSAGE_ARTIFACT}; "
            "the agent must submit commit_message before declaring completion"
        )
        _write_retry_hint(ctx, phase_name, detail)
        return [artifact_validation_failure_event(phase=phase_name, reason=detail)]

    message = _read_commit_message(ctx)
    if message is None or not message.strip():
        logger.warning(
            "{} agent produced unreadable or empty {}",
            phase_name,
            COMMIT_MESSAGE_ARTIFACT,
        )
        detail = (
            f"Invalid or empty commit_message artifact at {COMMIT_MESSAGE_ARTIFACT}; "
            "the agent must submit a readable commit_message payload "
            "before declaring completion"
        )
        _write_retry_hint(ctx, phase_name, detail)
        return [artifact_validation_failure_event(phase=phase_name, reason=detail)]

    if message.strip().lower().startswith("skip:"):
        clear_validation_retry_hint(
            ctx.workspace,
            phase_name,
            pipeline_policy=ctx.pipeline_policy,
        )
        logger.info("{}: commit agent requested skip — skipping", phase_name)
        return [PipelineEvent.COMMIT_SKIPPED]

    clear_validation_retry_hint(
        ctx.workspace,
        phase_name,
        pipeline_policy=ctx.pipeline_policy,
    )
    logger.info("{}: deferring commit execution to runner", phase_name)
    return []
