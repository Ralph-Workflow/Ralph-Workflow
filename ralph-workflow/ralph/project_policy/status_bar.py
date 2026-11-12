from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBarModel

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.workspace.scope import WorkspaceScope

NEUTRAL_PHASE_LABEL: str = "Running"


def format_readiness_footer_label(assessment: object | None) -> str:
    if assessment is None:
        return "Policy: -- -"
    s_attr: object = getattr(assessment, "structure_complete", False)
    i_attr: object = getattr(assessment, "implementation_verified", False)
    o_attr: object = getattr(assessment, "outcome_evidence_reviewed", False)
    s = "S*" if bool(s_attr) else "S-"
    i = "I*" if bool(i_attr) else "I-"
    o = "O*" if bool(o_attr) else "O-"
    return f"Policy: {s}/{i}/{o}"


_format_readiness_footer_label = format_readiness_footer_label


def push_readiness_assessment_status_bar(
    display: object,
    workspace_scope: WorkspaceScope,
    assessment: object | None,
    *,
    run_started_monotonic: float | None = None,
) -> None:
    label = _format_readiness_footer_label(assessment)
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_scope.root),
            phase_label=label,
            phase_style=phase_style_for_phase("policy_remediation"),
            run_started_monotonic=run_started_monotonic,
        )
        update = cast(
            "Callable[[object], None] | None",
            getattr(display, "update_status_bar", None),
        )
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug("readiness assessment status-bar push failed (non-fatal): {}", exc)


def push_remediation_status_bar(
    display: object,
    workspace_scope: WorkspaceScope,
    max_attempts: int,
    *,
    attempt: int = 1,
    elapsed_seconds: float | None = None,
    agent_name: str | None = None,
    run_started_monotonic: float | None = None,
) -> None:
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_scope.root),
            phase_label="Policy Remediation",
            phase_style=phase_style_for_phase("policy_remediation"),
            outer_dev_iteration=attempt,
            outer_dev_cap=max_attempts,
            outer_label="Remediation",
            elapsed_seconds=elapsed_seconds,
            agent_name=agent_name,
            run_started_monotonic=run_started_monotonic,
        )
        update_raw: object = getattr(display, "update_status_bar", None)
        update = cast("Callable[[object], None] | None", update_raw)
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug("remediation status-bar push failed (non-fatal): {}", exc)


def push_resumed_pipeline_status_bar(
    display: object,
    workspace_scope: WorkspaceScope,
    phase: str,
    *,
    run_started_monotonic: float | None = None,
) -> None:
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_scope.root),
            phase_label=phase.replace("_", " ").title(),
            phase_style=phase_style_for_phase(phase),
            run_started_monotonic=run_started_monotonic,
        )
        update = cast(
            "Callable[[object], None] | None", getattr(display, "update_status_bar", None)
        )
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug("resumed pipeline status-bar push failed (non-fatal): {}", exc)


def capture_status_bar_model(display: object) -> object | None:
    try:
        status_bar: object = getattr(display, "status_bar", None)
        if status_bar is None:
            return None
        model: object | None = getattr(status_bar, "last_model", None)
        return model
    except Exception as exc:
        logger.debug("remediation status-bar capture failed (non-fatal): {}", exc)
        return None


def restore_status_bar(display: object, model: object | None) -> None:
    if model is None:
        return
    try:
        update = cast(
            "Callable[[object], None] | None",
            getattr(display, "update_status_bar", None),
        )
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug("remediation status-bar restore failed (non-fatal): {}", exc)


def clear_remediation_status_bar(
    display: object,
    workspace_scope: WorkspaceScope,
    *,
    elapsed_seconds: float | None = None,
    run_started_monotonic: float | None = None,
) -> None:
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_scope.root),
            phase_label=NEUTRAL_PHASE_LABEL,
            phase_style=phase_style_for_phase(""),
            elapsed_seconds=elapsed_seconds,
            run_started_monotonic=run_started_monotonic,
        )
        update = cast(
            "Callable[[object], None] | None",
            getattr(display, "update_status_bar", None),
        )
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug("remediation status-bar clear failed (non-fatal): {}", exc)


@contextlib.contextmanager
def remediation_status_bar_session(
    display: object,
    workspace_scope: WorkspaceScope,
    *,
    resumed_phase: str | None = None,
) -> Iterator[None]:
    previous = capture_status_bar_model(display)
    try:
        yield
    finally:
        if previous is None:
            run_started: object | None = cast(
                "object | None", getattr(display, "run_started_monotonic", None)
            )
            clear_remediation_status_bar(
                display,
                workspace_scope,
                run_started_monotonic=(run_started if isinstance(run_started, float) else None),
            )
        else:
            restore_status_bar(display, previous)
        if resumed_phase is not None:
            resumed_run_started = cast(
                "object | None", getattr(display, "run_started_monotonic", None)
            )
            push_resumed_pipeline_status_bar(
                display,
                workspace_scope,
                resumed_phase,
                run_started_monotonic=(
                    resumed_run_started if isinstance(resumed_run_started, float) else None
                ),
            )


__all__ = [
    "NEUTRAL_PHASE_LABEL",
    "capture_status_bar_model",
    "clear_remediation_status_bar",
    "push_remediation_status_bar",
    "push_resumed_pipeline_status_bar",
    "remediation_status_bar_session",
    "restore_status_bar",
]
