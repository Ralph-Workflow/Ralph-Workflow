"""Operator-facing status for the conflict-resolution pipeline."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBarModel
from ralph.pipeline.conflict_resolution.attempt_fault import (
    ralph_origin_counts_as_liveness,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


PHASE_LABEL = "Rebase Conflict Resolution"
NEUTRAL_PHASE_LABEL = "Running"
_WARN_CHANNEL = "rebase-conflict"


@dataclass
class ResolutionStatusReporter:
    """Rate-limited human-readable conflict-resolution status publisher."""

    display: object
    target: str
    round_index: int
    round_cap: int
    stop_index: int | None
    stop_cap: int | None
    clock: Callable[[], float]
    interval_seconds: float
    started_at: float
    unresolved_paths: tuple[str, ...]
    agent_name: str | None = None
    _last_emitted_at: float | None = None

    def observe(self, event: object) -> None:
        """Publish a low-cadence status from a watchdog activity event."""
        now = self.clock()
        if self._last_emitted_at is not None and now - self._last_emitted_at < self.interval_seconds:
            return
        diagnostic = _status_diagnostic(event)
        kind = diagnostic.get("last_activity_kind", "none")
        if isinstance(kind, str) and not ralph_origin_counts_as_liveness(kind):
            return
        age = diagnostic.get("last_activity_age_seconds", "unknown")
        self._last_emitted_at = now
        emit_conflict_phase_line(
            self.display,
            "conflict resolution status: "
            f"round={self.round_index}/{self.round_cap}; agent={self.agent_name or 'pending'}; "
            f"last_activity_kind={kind}; last_activity_age_seconds={age}; "
            f"elapsed_seconds={max(0.0, now - self.started_at):.1f}; "
            f"unresolved_count={len(self.unresolved_paths)}",
        )

    def set_agent(self, agent_name: str) -> None:
        """Record the candidate currently performing resolution work."""
        self.agent_name = agent_name


def _status_diagnostic(event: object) -> dict[str, object]:
    raw: object = getattr(event, "diagnostic", {})
    return raw if isinstance(raw, dict) else {}


__all__ = [
    "NEUTRAL_PHASE_LABEL",
    "PHASE_LABEL",
    "ResolutionStatusReporter",
    "capture_status_bar_model",
    "clear_conflict_status_bar",
    "conflict_status_bar_session",
    "emit_conflict_phase_line",
    "push_conflict_status_bar",
    "restore_status_bar",
]


@contextlib.contextmanager
def conflict_status_bar_session(display: object, workspace_root: Path) -> Iterator[None]:
    """Capture the prior footer once and restore it after a whole rebase loop."""
    previous = capture_status_bar_model(display)
    try:
        yield
    finally:
        if previous is None:
            run_started = _display_run_started_monotonic(display)
            clear_conflict_status_bar(display, workspace_root, run_started_monotonic=run_started)
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
    elapsed_seconds: float | None = None,
    agent_name: str | None = None,
    run_started_monotonic: float | None = None,
) -> None:
    """Show the active conflict-resolution round in the persistent footer."""
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
            elapsed_seconds=elapsed_seconds,
            agent_name=agent_name,
            run_started_monotonic=run_started_monotonic,
        )
        _update_status_bar(display, model)
    except Exception as exc:
        logger.debug("conflict_resolution: status-bar push for '{}' failed: {}", target, exc)


def _phase_label(
    *,
    round_index: int,
    round_cap: int,
    stop_index: int | None,
    stop_cap: int | None,
    replay_index: int | None = None,
    replay_total: int | None = None,
) -> str:
    if replay_index is not None and replay_total is not None:
        return f"{PHASE_LABEL} (commit {replay_index}/{replay_total}, round {round_index}/{round_cap})"
    if stop_index is None or stop_cap is None:
        return PHASE_LABEL
    return f"{PHASE_LABEL} (commit {stop_index}/{stop_cap}, round {round_index}/{round_cap})"


def capture_status_bar_model(display: object) -> object | None:
    """Return the currently displayed footer model, when one is available."""
    try:
        status_bar: object = getattr(display, "status_bar", None)
        if status_bar is None:
            return None
        model: object = getattr(status_bar, "last_model", None)
        return model
    except Exception as exc:
        logger.debug("conflict_resolution: status-bar capture failed: {}", exc)
        return None


def clear_conflict_status_bar(
    display: object,
    workspace_root: Path,
    *,
    elapsed_seconds: float | None = None,
    run_started_monotonic: float | None = None,
) -> None:
    """Replace an unowned resolution footer with a neutral running footer."""
    try:
        _update_status_bar(
            display,
            StatusBarModel(
                workspace_root=str(workspace_root),
                phase_label=NEUTRAL_PHASE_LABEL,
                phase_style=phase_style_for_phase(""),
                elapsed_seconds=elapsed_seconds,
                run_started_monotonic=run_started_monotonic,
            ),
        )
    except Exception as exc:
        logger.debug("conflict_resolution: status-bar clear failed: {}", exc)


def restore_status_bar(display: object, model: object | None) -> None:
    """Restore a captured footer without letting presentation alter integration."""
    if model is None:
        return
    try:
        _update_status_bar(display, model)
    except Exception as exc:
        logger.debug("conflict_resolution: status-bar restore failed: {}", exc)


def _update_status_bar(display: object, model: object) -> None:
    update = cast("Callable[[object], None] | None", getattr(display, "update_status_bar", None))
    if update is not None:
        update(model)


def _display_run_started_monotonic(display: object) -> float | None:
    value: object = getattr(display, "run_started_monotonic", None)
    return value if isinstance(value, float) else None


def emit_conflict_phase_line(display: object, message: str) -> None:
    """Emit one operator-visible conflict-resolution transcript line."""
    with contextlib.suppress(Exception):
        emit = cast("Callable[[str, str, str], None] | None", getattr(display, "emit_warn_line", None))
        if emit is not None:
            emit("run", _WARN_CHANNEL, message)
