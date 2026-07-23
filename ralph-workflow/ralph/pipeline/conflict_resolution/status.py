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
    from collections.abc import Callable, Iterator
    from pathlib import Path

#: Footer label shown while the resolution pipeline owns the run.
PHASE_LABEL = "Rebase Conflict Resolution"

#: Neutral footer label pushed when the resolution pipeline exits and
#: there was no prior model to restore. Deliberately generic: this
#: module cannot know which phase the run will resume into, and any
#: label is better than leaving the footer claiming a resolution that
#: has finished.
NEUTRAL_PHASE_LABEL = "Running"

#: Channel used for the pipeline's operator-facing warn lines.
_WARN_CHANNEL = "rebase-conflict"

__all__ = [
    "NEUTRAL_PHASE_LABEL",
    "PHASE_LABEL",
    "capture_status_bar_model",
    "clear_conflict_status_bar",
    "conflict_status_bar_session",
    "emit_conflict_phase_line",
    "push_conflict_status_bar",
    "restore_status_bar",
]


@contextlib.contextmanager
def conflict_status_bar_session(
    display: object, workspace_root: Path
) -> Iterator[None]:
    """Own the footer for a whole resolution loop: capture once, restore once.

    A rebase resolution works through several stops, each of which pushes
    its own footer model. Capturing per stop would capture the CONFLICT
    bar pushed by the previous stop, so the final restore would put the
    resolution label back and leave it pinned after the loop ended --
    the display equivalent of the hang this phase label exists to rule
    out. Entering the context once around the entire loop captures the
    genuinely pre-resolution model.

    Restores on exception too, so a loop that raises still hands the
    footer back.
    """
    previous = capture_status_bar_model(display)
    try:
        yield
    finally:
        if previous is None:
            clear_conflict_status_bar(display, workspace_root)
        else:
            restore_status_bar(display, previous)


def push_conflict_status_bar(
    display: object,
    workspace_root: Path,
    *,
    target: str,
    round_index: int,
    round_cap: int,
    stop_index: int | None = None,
    stop_cap: int | None = None,
    replay_index: int | None = None,
    replay_total: int | None = None,
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
        stop_index: 1-based index of the rebase stop being resolved, when
            the resolution is driving a rebase rather than a single
            endpoint merge. ``None`` keeps the footer byte-identical to
            the merge-mode label.
        stop_cap: Total rebase stops allowed.
        replay_index: 1-based position of the commit the paused rebase is
            replaying, when git's rebase state could be read.
        replay_total: Number of commits that rebase is replaying in all.
    """
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_root),
            phase_label=_phase_label(
                round_index=round_index,
                round_cap=round_cap,
                stop_index=stop_index,
                stop_cap=stop_cap,
                replay_index=replay_index,
                replay_total=replay_total,
            ),
            phase_style=phase_style_for_phase(PHASE_RESOLUTION),
            outer_dev_iteration=round_index,
            outer_dev_cap=round_cap,
            outer_label="Round",
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


def _phase_label(
    *,
    round_index: int,
    round_cap: int,
    stop_index: int | None,
    stop_cap: int | None,
    replay_index: int | None = None,
    replay_total: int | None = None,
) -> str:
    """Footer label, widened with the commit counter only in rebase mode.

    A rebase resolution can span many commits, so 'round 2/3' alone tells
    the operator nothing about how far through the replay the run is --
    it looks identical on stop 1 and stop 9.

    The commit counter prefers the REPLAY position
    (``replay_index``/``replay_total``), which is read from git's own
    rebase state and is what the operator means by "which commit". It
    falls back to the bounded loop's stop counters when that state was
    unreadable, and to the bare label when neither pair is available.
    Those are different numbers: ``stop_cap`` is a fixed safety bound on
    how many stops this loop will service, not the length of the rebase.
    """
    if replay_index is not None and replay_total is not None:
        return (
            f"{PHASE_LABEL} (commit {replay_index}/{replay_total}, "
            f"round {round_index}/{round_cap})"
        )
    if stop_index is None or stop_cap is None:
        return PHASE_LABEL
    return (
        f"{PHASE_LABEL} (commit {stop_index}/{stop_cap}, "
        f"round {round_index}/{round_cap})"
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


def clear_conflict_status_bar(display: object, workspace_root: Path) -> None:
    """Push a neutral footer when there is no prior model to restore.

    The resolution pipeline is entered from four seams, including the
    startup seam, where the next run-loop status-bar push can be a whole
    phase away. Leaving the footer on
    :data:`PHASE_LABEL` for that long tells the operator a resolution is
    still running when it has already finished -- which reads exactly
    like the hang this phase label exists to rule out.

    Defensive by contract, like every function here: a display that
    raises is logged at DEBUG and otherwise ignored.
    """
    try:
        model = StatusBarModel(
            workspace_root=str(workspace_root),
            phase_label=NEUTRAL_PHASE_LABEL,
            phase_style=phase_style_for_phase(""),
        )
        update = cast(
            "Callable[[object], None] | None",
            getattr(display, "update_status_bar", None),
        )
        if update is not None:
            update(model)
    except Exception as exc:
        logger.debug(
            "conflict_resolution: status-bar clear failed (non-fatal): {}", exc
        )


def restore_status_bar(display: object, model: object | None) -> None:
    """Put the pre-resolution footer model back. Never raises.

    A ``None`` model means the display exposed no readable footer to
    capture, so there is nothing to restore verbatim; the caller uses
    :func:`clear_conflict_status_bar` for that case rather than leaving
    the resolution label stranded.
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
