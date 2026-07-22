"""Operator-facing surface for the conflict-resolution pipeline.

While the resolution pipeline runs, the persistent footer must say so:
the run is stopped on a conflicted merge, an agent is editing files, and
that can take minutes. Without a dedicated phase label the footer keeps
showing whatever phase the run was in when the seam fired, which reads as
a hang.

Every function here is defensive by contract, exactly as
``ralph.project_policy.cli_integration._push_remediation_status_bar`` is:
presentation must NEVER block integration. A display that raises is
logged at DEBUG and otherwise ignored.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBarModel
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

#: Footer label shown while the resolution pipeline owns the run.
PHASE_LABEL = "Rebase Conflict Resolution"

#: Channel used for the pipeline's operator-facing warn lines.
_WARN_CHANNEL = "rebase-conflict"

__all__ = [
    "PHASE_LABEL",
    "capture_status_bar_model",
    "emit_conflict_phase_line",
    "push_conflict_status_bar",
    "restore_status_bar",
]


def push_conflict_status_bar(
    display: object,
    workspace_root: Path,
    *,
    target: str,
    round_index: int,
    round_cap: int,
) -> None:
    """Show the resolution phase and its round counter in the footer.

    Args:
        display: The active display, or any object; a display without an
            ``update_status_bar`` callable is silently tolerated.
        workspace_root: Repository root shown in the footer.
        target: Mainline branch being merged in. Recorded on the log line
            so a stuck resolution is attributable.
        round_index: 1-based index of the round about to run.
        round_cap: Total rounds allowed.
    """
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_root),
            phase_label=PHASE_LABEL,
            phase_style=phase_style_for_phase(PHASE_RESOLUTION),
            outer_dev_iteration=round_index,
            outer_dev_cap=round_cap,
        )
        update = cast(
            "Callable[[object], None] | None",
            getattr(display, "update_status_bar", None),
        )
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug(
            "conflict_resolution: status-bar push for '{}' failed (non-fatal): {}",
            target,
            exc,
        )


def capture_status_bar_model(display: object) -> object | None:
    """Read the model currently in the footer so it can be restored.

    Returns ``None`` when the display exposes no readable status bar, in
    which case :func:`restore_status_bar` is a no-op and the run loop
    re-pushes its own model on the next iteration.
    """
    try:
        status_bar: object = getattr(display, "status_bar", None)
        if status_bar is None:
            return None
        model: object = getattr(status_bar, "last_model", None)
        return model
    except Exception as exc:
        logger.debug(
            "conflict_resolution: status-bar capture failed (non-fatal): {}", exc
        )
        return None


def restore_status_bar(display: object, model: object | None) -> None:
    """Put the pre-resolution footer model back. Never raises.

    A ``None`` model leaves the resolution label in place; the run loop
    re-pushes the surrounding phase on its next iteration, so the label
    is transient either way.
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
        logger.debug(
            "conflict_resolution: status-bar restore failed (non-fatal): {}", exc
        )


def emit_conflict_phase_line(display: object, message: str) -> None:
    """Emit an operator-facing warn line for the resolution pipeline."""
    with contextlib.suppress(Exception):
        emit = cast(
            "Callable[[str, str, str], None] | None",
            getattr(display, "emit_warn_line", None),
        )
        if emit is not None:
            emit("run", _WARN_CHANNEL, message)
