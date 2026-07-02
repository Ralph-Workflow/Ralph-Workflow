"""Canonical presentation formatters for phase lifecycle rendering.

This is the single source of truth for how iteration context (dev cycles,
analysis cycles) and phase outcomes are labeled across
phase-start banners, phase-close lines, and run-end summaries.

All formatters are pure: they accept simple values and return strings.
No Console construction, no env reads, no pipeline logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    """Return canonical label for outer development cycle number (1-indexed).

    When *cap* is provided (and positive), shows ``Dev N/cap`` to make the
    remaining budget immediately visible.  Without a cap, shows ``Dev #N``.
    """
    if cap is not None and cap > 0:
        return f"Dev {n}/{cap}"
    return f"Dev #{n}"


def format_analysis_cycle(n: int, cap: int | None = None) -> str:
    """Return canonical label for inner analysis cycle (1-indexed)."""
    if cap is not None:
        return f"Analysis {n}/{cap}"
    return f"Analysis #{n}"


def format_dev_cycle_compact(n: int, cap: int | None = None) -> str:
    """Return compact dev cycle label for narrow-terminal rendering.

    The compact form shortens the canonical ``Dev N/cap`` to ``D1/3``
    (4 chars) so the persistent Status Bar fits a constrained terminal
    without dropping the iteration field. Without a cap, returns
    ``D#1`` (3 chars).

    The compact form keeps the disambiguating ``D`` prefix so an
    operator can still tell dev cycles from analysis cycles at a
    glance. Used by :mod:`ralph.display.status_bar` when the canonical
    label exceeds the per-iteration label budget derived from
    ``ctx.width``.
    """
    if cap is not None and cap > 0:
        return f"D{n}/{cap}"
    return f"D#{n}"


def format_analysis_cycle_compact(n: int, cap: int | None = None) -> str:
    """Return compact analysis cycle label for narrow-terminal rendering.

    Shortens ``Analysis N/cap`` to ``A1/3`` (4 chars) and ``Analysis #N``
    to ``A#1`` (3 chars). The ``A`` prefix keeps the label distinct
    from the dev-cycle compact form.
    """
    if cap is not None and cap > 0:
        return f"A{n}/{cap}"
    return f"A#{n}"


def format_dev_cycle_minimal(n: int, cap: int | None = None) -> str:
    """Return minimal dev cycle label for very narrow terminals.

    Returns ``N/cap`` (no prefix) when a cap is provided, ``#N``
    otherwise. Used by :mod:`ralph.display.status_bar` when even the
    compact form (``D1/3``) cannot fit.
    """
    if cap is not None and cap > 0:
        return f"{n}/{cap}"
    return f"#{n}"


def format_analysis_cycle_minimal(n: int, cap: int | None = None) -> str:
    """Return minimal analysis cycle label for very narrow terminals.

    Returns ``N/cap`` (no prefix) when a cap is provided, ``#N``
    otherwise. The compact and minimal forms of the analysis cycle share
    the same shape (the ``A`` prefix is dropped); at very narrow widths
    the operator still sees the count vs cap and a glyph prefix
    distinguishes dev vs analysis.
    """
    if cap is not None and cap > 0:
        return f"{n}/{cap}"
    return f"#{n}"


def format_elapsed_seconds(s: float) -> str:
    """Return canonical elapsed-time label."""
    return f"{round(s, 1)}s"


def format_exit_trigger(snapshot: _ExitState) -> str:
    """Return canonical exit-trigger label from a PipelineSnapshot-like object."""
    if snapshot.interrupted_by_user:
        return "interrupted"
    if snapshot.is_terminal_success:
        return "completed"
    if snapshot.is_terminal_failure:
        return "failed"
    return "exited"


def format_transition_context_items(context: dict[str, object]) -> list[str]:
    """Return formatted display strings for a phase transition context dict.

    Normalizes context items from generic key=value to canonical display format:
    - 'analysis_status' key: rendered as the bare value (no key prefix)
    - 'decision' key: rendered as '→ {value}' (arrow notation)
    - multi-word keys (containing spaces): rendered as '[key value]' bracket notation
    - all other keys: rendered as 'key=value'
    """
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
    """Canonical iteration context for phase start/close rendering.

    Attributes:
        outer_dev: Outer development cycle number (None if not in outer loop).
        outer_dev_cap: Budget cap for outer dev cycles (shows Dev N/cap when set).
        inner_analysis: Inner analysis cycle number (None if not in analysis).
        inner_analysis_cap: Max inner analysis cycles (None if unknown).
    """

    outer_dev: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None

    def has_context(self) -> bool:
        """Return True if any iteration context is set."""
        return any(x is not None for x in (self.outer_dev, self.inner_analysis))

    def context_labels(self) -> list[tuple[str, str]]:
        """Return (label, style_key) pairs for rendering, in display priority order.

        Order: outer dev (highest visibility) → inner analysis.
        """
        parts: list[tuple[str, str]] = []
        if self.outer_dev is not None:
            parts.append((format_dev_cycle(self.outer_dev, self.outer_dev_cap), "theme.outer_dev"))
        if self.inner_analysis is not None:
            label = format_analysis_cycle(self.inner_analysis, self.inner_analysis_cap)
            parts.append((label, "theme.inner_analysis"))
        return parts


__all__ = [
    "PhaseIterationContext",
    "format_analysis_cycle",
    "format_analysis_cycle_compact",
    "format_analysis_cycle_minimal",
    "format_dev_cycle",
    "format_dev_cycle_compact",
    "format_dev_cycle_minimal",
    "format_elapsed_seconds",
    "format_exit_trigger",
    "format_transition_context_items",
]
