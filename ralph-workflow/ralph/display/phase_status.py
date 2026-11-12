from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.display.auto_integrate_message import (
    IntegrationDisplayLabel,
    format_integration_label,
)

_SECONDS_PER_MINUTE = 60

if TYPE_CHECKING:
    from typing import Protocol

    class _ExitState(Protocol):
        @property
        def interrupted_by_user(self) -> bool: ...

        @property
        def is_terminal_success(self) -> bool: ...

        @property
        def is_terminal_failure(self) -> bool: ...


def format_dev_cycle(n: int, cap: int | None = None) -> str:
    if cap is not None and cap > 0:
        return f"Cycle {n}/{cap}"
    return f"Cycle #{n}"


def format_analysis_cycle(n: int, cap: int | None = None) -> str:
    if cap is not None and cap > 0:
        return f"iter {n}/{cap}"
    return f"iter #{n}"


def format_dev_cycle_compact(n: int, cap: int | None = None) -> str:
    if cap is not None and cap > 0:
        return f"C{n}/{cap}"
    return f"C#{n}"


def format_analysis_cycle_compact(n: int, cap: int | None = None) -> str:
    if cap is not None and cap > 0:
        return f"i{n}/{cap}"
    return f"i#{n}"


def format_dev_cycle_minimal(n: int, cap: int | None = None) -> str:
    if cap is not None and cap > 0:
        return f"{n}/{cap}"
    return f"#{n}"


def format_analysis_cycle_minimal(n: int, cap: int | None = None) -> str:
    if cap is not None and cap > 0:
        return f"{n}/{cap}"
    return f"#{n}"


def format_elapsed_seconds(s: float) -> str:
    rounded = round(max(0.0, s), 1)
    whole_seconds = int(rounded)
    if whole_seconds >= _SECONDS_PER_MINUTE:
        minutes, remainder = divmod(whole_seconds, _SECONDS_PER_MINUTE)
        fraction = rounded - whole_seconds
        seconds_label = f"{remainder:02d}" if fraction == 0 else f"{remainder + fraction:04.1f}"
        return f"{minutes}m{seconds_label}s"
    return f"{whole_seconds}s" if rounded == whole_seconds else f"{rounded}s"


def format_exit_trigger(snapshot: _ExitState) -> str:
    if snapshot.interrupted_by_user:
        return "interrupted"
    if snapshot.is_terminal_success:
        return "completed"
    if snapshot.is_terminal_failure:
        return "failed"
    return "exited"


def format_transition_context_items(context: dict[str, object]) -> list[str]:
    parts: list[str] = []
    for k, v in context.items():
        v_str = str(v)
        if k == "analysis_status":
            parts.append(v_str)
        elif k == "decision":
            parts.append(f"→ {v_str}")
        elif " " in k:
            parts.append(f"[{k} {v_str}]")
        else:
            parts.append(f"{k}={v_str}")
    return parts


@dataclass(frozen=True)
class PhaseIterationContext:
    outer_dev: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None

    def has_context(self) -> bool:
        return any(x is not None for x in (self.outer_dev, self.inner_analysis))

    def context_labels(self) -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []
        if self.outer_dev is not None:
            parts.append((format_dev_cycle(self.outer_dev, self.outer_dev_cap), "theme.outer_dev"))
        if self.inner_analysis is not None:
            label = format_analysis_cycle(self.inner_analysis, self.inner_analysis_cap)
            parts.append((label, "theme.inner_analysis"))
        return parts


def format_integration_phase_label(
    action_or_state: object,
    *,
    is_diagnosis: bool = False,
    detail: str | None = None,
) -> str:
    base = format_integration_label(action_or_state, is_diagnosis=is_diagnosis)
    title_label = base.title()
    if detail:
        return f"{title_label} ({detail})"
    return title_label


__all__ = [
    "IntegrationDisplayLabel",
    "PhaseIterationContext",
    "format_analysis_cycle",
    "format_analysis_cycle_compact",
    "format_analysis_cycle_minimal",
    "format_dev_cycle",
    "format_dev_cycle_compact",
    "format_dev_cycle_minimal",
    "format_elapsed_seconds",
    "format_exit_trigger",
    "format_integration_label",
    "format_integration_phase_label",
    "format_transition_context_items",
]
