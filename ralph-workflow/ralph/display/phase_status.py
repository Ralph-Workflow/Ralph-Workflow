"""Canonical presentation formatters for phase lifecycle rendering.

This is the single source of truth for how iteration context (dev cycles,
analysis cycles, fixer cycles) and phase outcomes are labeled across
phase-start banners, phase-close lines, and run-end summaries.

All formatters are pure: they accept simple values and return strings.
No Console construction, no env reads, no pipeline logic.
"""

from __future__ import annotations

from dataclasses import dataclass


def format_dev_cycle(n: int) -> str:
    """Return canonical label for outer development cycle number (1-indexed)."""
    return f"Dev #{n}"


def format_analysis_cycle(n: int, cap: int | None = None) -> str:
    """Return canonical label for inner analysis cycle (1-indexed)."""
    if cap is not None:
        return f"Analysis {n}/{cap}"
    return f"Analysis #{n}"


def format_fixer_cycle(n: int) -> str:
    """Return canonical label for fixer cycle number (1-indexed)."""
    return f"Fixer #{n}"


def format_budget_remaining(n: int) -> str:
    """Return canonical label for remaining budget counter."""
    return f"Budget: {n} left"


def format_elapsed_seconds(s: float) -> str:
    """Return canonical elapsed-time label."""
    return f"{round(s, 1)}s"


def format_transition_context_items(context: dict[str, object]) -> list[str]:
    """Return formatted display strings for a phase transition context dict.

    Normalizes context items from generic key=value to canonical display format:
    - 'analysis_status' key: rendered as the bare value (no key prefix)
    - 'decision' key: rendered as '→ {value}' (arrow notation)
    - keys ending in '_budget': rendered as canonical 'Budget: N left' label
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
        elif k.endswith("_budget"):
            if v_str.endswith(" remaining"):
                try:
                    n = int(v_str.split(maxsplit=1)[0])
                    parts.append(format_budget_remaining(n))
                except (ValueError, IndexError):
                    parts.append(f"Budget: {v_str}")
            else:
                parts.append(f"Budget: {v_str}")
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
        inner_analysis: Inner analysis cycle number (None if not in analysis).
        inner_analysis_cap: Max inner analysis cycles (None if unknown).
        fixer: Fixer cycle number (None if not in fixer context).
        budget_remaining: Remaining budget (None if not tracked).
    """

    outer_dev: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None
    fixer: int | None = None
    budget_remaining: int | None = None

    def has_context(self) -> bool:
        """Return True if any iteration context is set."""
        return any(
            x is not None
            for x in (self.outer_dev, self.inner_analysis, self.fixer, self.budget_remaining)
        )

    def context_labels(self) -> list[tuple[str, str]]:
        """Return (label, style_key) pairs for rendering, in display priority order.

        Order: outer dev (highest visibility) → inner analysis → fixer → budget.
        """
        parts: list[tuple[str, str]] = []
        if self.outer_dev is not None:
            parts.append((format_dev_cycle(self.outer_dev), "theme.outer_dev"))
        if self.inner_analysis is not None:
            label = format_analysis_cycle(self.inner_analysis, self.inner_analysis_cap)
            parts.append((label, "theme.inner_analysis"))
        if self.fixer is not None:
            parts.append((format_fixer_cycle(self.fixer), "theme.fixer_iteration"))
        if self.budget_remaining is not None:
            parts.append((format_budget_remaining(self.budget_remaining), "theme.level.warn"))
        return parts


__all__ = [
    "PhaseIterationContext",
    "format_analysis_cycle",
    "format_budget_remaining",
    "format_dev_cycle",
    "format_elapsed_seconds",
    "format_fixer_cycle",
    "format_transition_context_items",
]
