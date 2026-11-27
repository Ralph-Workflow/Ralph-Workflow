from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBarModel
from ralph.pipeline._integration_status import build_integration_status
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.status_bar_authority import operation_status, show_operation_status

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ralph.pipeline._integration_status import IntegrationStatus


PHASE_LABEL = "Rebase Conflict Resolution"
DIAGNOSIS_PHASE_LABEL = "Evidence Diagnosis"
NEUTRAL_PHASE_LABEL = "Running"
_WARN_CHANNEL = "rebase-conflict"
_DIAGNOSIS_WARN_CHANNEL = "evidence-diagnosis"


@dataclass
class ResolutionStatusReporter:
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
    initial_conflicted_count: int | None = None
    agent_name: str | None = None
    _last_emitted_at: float | None = None
    is_diagnosis: bool = False
    action: object = None
    operation_display_state: str | None = None

    def observe(self, event: object) -> None:
        now = self.clock()
        if (
            self._last_emitted_at is not None
            and now - self._last_emitted_at < self.interval_seconds
        ):
            return
        diagnostic = _status_diagnostic(event)
        kind = diagnostic.get("last_activity_kind", "none")
        age = diagnostic.get("last_activity_age_seconds", "unknown")
        self._last_emitted_at = now
        diag = (
            self.is_diagnosis
            or self.operation_display_state == "evidence_diagnosis"
            or (self.action is not None and "diagnos" in str(self.action).lower())
        )
        label = "evidence diagnosis" if diag else "conflict resolution"
        channel = _DIAGNOSIS_WARN_CHANNEL if diag else _WARN_CHANNEL
        progress_status = build_integration_status(
            cause="PROGRESS",
            done_count=(
                None
                if self.initial_conflicted_count is None
                else max(self.initial_conflicted_count - len(self.unresolved_paths), 0)
            ),
            remaining_count=len(self.unresolved_paths),
        )
        emit_conflict_phase_line(
            self.display,
            f"{label} status: "
            f"round={min(self.round_index, self.round_cap)}/{self.round_cap}; agent={self.agent_name or 'pending'}; "
            f"last_activity_kind={kind}; last_activity_age_seconds={age}; "
            f"elapsed_seconds={max(0.0, now - self.started_at):.1f}; "
            f"last_progress={progress_status.progress}",
            channel=channel,
        )

    def set_agent(self, agent_name: str) -> None:
        self.agent_name = agent_name


def _status_diagnostic(event: object) -> dict[str, object]:
    try:
        raw: object = object.__getattribute__(event, "diagnostic")
    except AttributeError:
        raw = {}
    return raw if isinstance(raw, dict) else {}


__all__ = [
    "DIAGNOSIS_PHASE_LABEL",
    "NEUTRAL_PHASE_LABEL",
    "PHASE_LABEL",
    "ResolutionStatusReporter",
    "conflict_status_bar_session",
    "emit_conflict_phase_line",
    "phase_label",
    "push_conflict_status_bar",
]


@contextlib.contextmanager
def conflict_status_bar_session(display: object, workspace_root: Path) -> Iterator[None]:
    idle = StatusBarModel(
        workspace_root=str(workspace_root),
        phase_label=NEUTRAL_PHASE_LABEL,
        phase_style=phase_style_for_phase(""),
        run_started_monotonic=_display_run_started_monotonic(display),
    )
    with operation_status(display, idle=idle):
        yield


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
    action: object = None,
    operation_display_state: str | None = None,
    is_diagnosis: bool = False,
    integration_status: IntegrationStatus | None = None,
) -> None:
    try:
        diag = (
            is_diagnosis
            or operation_display_state == "evidence_diagnosis"
            or (action is not None and "diagnos" in str(action).lower())
        )
        display_state = (
            "evidence_diagnosis" if diag else (operation_display_state or "conflict_resolution")
        )
        model = StatusBarModel(
            workspace_root=str(workspace_root),
            phase_label=_phase_label(
                round_index=round_index,
                round_cap=round_cap,
                stop_index=stop_index,
                stop_cap=stop_cap,
                replay_index=replay_index,
                replay_total=replay_total,
                is_diagnosis=diag,
            ),
            phase_style=("theme.status.info" if diag else phase_style_for_phase(PHASE_RESOLUTION)),
            outer_dev_iteration=round_index,
            outer_dev_cap=round_cap,
            outer_label="Diagnosis" if diag else "Round",
            elapsed_seconds=elapsed_seconds,
            agent_name=agent_name,
            run_started_monotonic=run_started_monotonic,
            operation_display_state=display_state,
            integration_status=integration_status,
        )
        show_operation_status(display, model)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.debug("conflict_resolution: status-bar push for '{}' failed: {}", target, exc)


def _phase_label(
    *,
    round_index: int,
    round_cap: int,
    stop_index: int | None,
    stop_cap: int | None,
    replay_index: int | None = None,
    replay_total: int | None = None,
    is_diagnosis: bool = False,
    phase_label_override: str | None = None,
) -> str:
    base = phase_label_override or (DIAGNOSIS_PHASE_LABEL if is_diagnosis else PHASE_LABEL)
    round_index = min(round_index, round_cap)
    if replay_index is not None and replay_total is not None:
        return (
            f"{base} (commit {min(replay_index, replay_total)}/{replay_total}, "
            f"round {round_index}/{round_cap})"
        )
    if stop_index is None or stop_cap is None:
        return base
    return (
        f"{base} (commit {min(stop_index, stop_cap)}/{stop_cap}, round {round_index}/{round_cap})"
    )


phase_label = _phase_label


def _display_run_started_monotonic(display: object) -> float | None:
    try:
        value: object = object.__getattribute__(display, "run_started_monotonic")
    except AttributeError:
        value = None
    return value if isinstance(value, float) else None


def emit_conflict_phase_line(
    display: object, message: str, *, channel: str = _WARN_CHANNEL
) -> None:
    with contextlib.suppress(Exception):
        emit = cast(
            "Callable[[str, str, str], None] | None", getattr(display, "emit_warn_line", None)
        )
        if emit is not None:
            emit("run", channel, message)
