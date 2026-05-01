"""Contract test: phase banner must not rely on scalar iteration/reviewer_pass fields.

show_phase_start_from_state must work using only the generic dict-based
state API (budget_caps, outer_progress), not the removed scalar fields.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.phase_banner import show_phase_start_from_state


def _make_display_context(buf: StringIO):
    console = Console(file=buf, markup=False, highlight=False, width=100)
    return make_display_context(console=console, force_mode="medium")


def _make_state_with_custom_counter(counter_name: str, completed: int, cap: int) -> MagicMock:
    """Return a mock state that only has the generic dict-based counter fields."""
    state = MagicMock(spec=[])  # strict spec — no implicit attributes
    state.budget_caps = {counter_name: cap}
    state.outer_progress = {counter_name: completed}
    # Deliberately DO NOT set state.iteration or state.reviewer_pass
    return state


def test_show_phase_start_from_state_uses_budget_caps_not_scalar_fields() -> None:
    """show_phase_start_from_state works with a state that has no scalar fields."""
    buf = StringIO()
    ctx = _make_display_context(buf)
    state = _make_state_with_custom_counter("custom_cycles", 2, 5)

    # Must not raise AttributeError for missing 'iteration' or 'reviewer_pass'
    show_phase_start_from_state(state, "work_phase", display_context=ctx)

    output = buf.getvalue()
    # The banner must be rendered without error
    assert "Work Phase" in output or "work_phase" in output.lower()
