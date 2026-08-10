"""Status-bar push helper for the project-policy-readiness pipeline.

Lives in its own module so :mod:stays under the 1000-line repository cap while still letting the
remediation phase surface a live Remediation N/Max label in the
persistent footer (the operator-grade fix the wt-028-display consolidation
introduced for policy-remediation). The helper is intentionally
defensive: any display failure is swallowed -- presentation must NEVER
block remediation.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBarModel

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.workspace.scope import WorkspaceScope

#: Neutral footer label pushed when the policy pipeline exits and there
#: was no prior model to restore. Mirrors the same hand-back contract the
#: conflict-resolution pipeline uses: deliberately generic because this
#: module cannot know which phase the run will resume into, and any
#: label is better than leaving the footer claiming a remediation that
#: has finished.
NEUTRAL_PHASE_LABEL: str = "Running"


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
    """Seed the persistent status bar for the remediation phase.

    Mirrors the run loop phase push so the footer shows the working
    directory and the active phase during remediation instead of nothing.
    attempt is the 1-indexed live attempt (1 on the first try, 2 on
    the first re-try, etc.) so the bar surfaces real progress instead
    of a hardcoded Dev 1/N placeholder. Defensive: any display
    failure is swallowed -- presentation must never block remediation.
    """
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
    except Exception as exc:  # defensive: presentation must never block remediation
        logger.debug("remediation status-bar push failed (non-fatal): {}", exc)


def capture_status_bar_model(display: object) -> object | None:
    """Read the footer model currently displayed so it can be restored.

    Returns ``None`` when the display exposes no readable status bar,
    in which case the run loop re-pushes its own model on the next
    iteration. Mirrors the same shape the conflict-resolution
    pipeline uses for the same hand-back contract.
    """
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
    """Put the pre-remediation footer model back. Never raises.

    A ``None`` model means the display exposed no readable footer to
    capture, so there is nothing to restore verbatim; the caller uses
    :func:`clear_remediation_status_bar` for that case rather than
    leaving the remediation label stranded.
    """
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
    """Push a neutral footer when there is no prior model to restore.

    The policy pipeline is entered from the run-loop startup seam,
    where the next run-loop status-bar push can be a whole phase
    away. Leaving the footer on ``Policy Remediation`` for that long
    tells the operator a remediation is still running when it has
    already finished -- which reads exactly like the hang this phase
    label exists to rule out. Defensive: a display that raises is
    logged at DEBUG and otherwise ignored.
    """
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
) -> Iterator[None]:
    """Own the footer for a whole policy pipeline: capture once, restore once.

    The policy pipeline pushes its own footer model on every
    remediation iteration (so the live ``Remediation N/Max`` label
    updates). Capturing AFTER those pushes would capture the
    remediation label itself, and the final restore would put the
    label back and leave it pinned after the pipeline ended -- the
    display equivalent of the hang this phase label exists to rule
    out. Entering the context once around the entire pipeline (i.e.
    BEFORE the first push) captures the genuinely pre-policy model.

    Restores on exception too, so a failing policy run hands the
    footer back exactly like a successful one. Mirrors the shape
    ``conflict_status_bar_session`` already settled on for the other
    out-of-graph seam.
    """
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
                run_started_monotonic=(
                    run_started if isinstance(run_started, float) else None
                ),
            )
        else:
            restore_status_bar(display, previous)


__all__ = [
    "NEUTRAL_PHASE_LABEL",
    "capture_status_bar_model",
    "clear_remediation_status_bar",
    "push_remediation_status_bar",
    "remediation_status_bar_session",
    "restore_status_bar",
]
