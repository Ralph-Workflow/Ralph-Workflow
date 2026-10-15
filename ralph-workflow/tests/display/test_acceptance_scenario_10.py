"""Scenario 10 acceptance: canonical display vocabularies yield distinct operator outputs.

Every one of the eight ``OperationDisplayState`` values and the seven
``IntegrationDisplayLabel`` values must produce a DISTINCT rendering on
each operator surface:

1. ``format_auto_integrate_message`` (the live line / completion summary),
2. ``format_integration_label`` (the canonical phase label), and
3. the persistent Status Bar render (``render_status_bar``).

If any two inputs collapse to the same output on any surface, the
vocabulary has drifted and these tests fail loudly rather than
weakening the assertion.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from ralph.display.auto_integrate_message import (
    OperationDisplayState,
    format_auto_integrate_message,
    format_integration_label,
)
from ralph.display.context import make_display_context
from ralph.display.integration_display_label import IntegrationDisplayLabel
from ralph.display.status_bar import StatusBarModel, render_status_bar

ALL_OPERATION_STATES = list(OperationDisplayState)
ALL_DISPLAY_LABELS = list(IntegrationDisplayLabel)

assert len(ALL_OPERATION_STATES) == 8, (
    f"OperationDisplayState must have exactly 8 members, found {len(ALL_OPERATION_STATES)}"
)
assert len(ALL_DISPLAY_LABELS) == 7, (
    f"IntegrationDisplayLabel must have exactly 7 members, found {len(ALL_DISPLAY_LABELS)}"
)


def _render_status_bar_for(state: str | None) -> str:
    console = Console(width=120, force_terminal=False)
    ctx = make_display_context(console=console, env={})
    model = StatusBarModel(
        workspace_root="/home/user/repo",
        phase_label="Active Phase",
        phase_style="theme.phase.development",
        operation_display_state=state,
    )
    return render_status_bar(model, ctx, home="/home/user").plain


def _ids(values: list[str]) -> list[str]:
    return [v.replace("-", "_") for v in values]


@pytest.mark.parametrize("state", ALL_OPERATION_STATES, ids=_ids(ALL_OPERATION_STATES))
def test_operation_display_state_distinct_auto_integrate_message(
    state: OperationDisplayState,
) -> None:
    message = format_auto_integrate_message(operation_display_state=state)
    others = [
        format_auto_integrate_message(operation_display_state=s)
        for s in ALL_OPERATION_STATES
        if s is not state
    ]
    assert message not in others


@pytest.mark.parametrize("state", ALL_OPERATION_STATES, ids=_ids(ALL_OPERATION_STATES))
def test_operation_display_state_distinct_status_bar(state: OperationDisplayState) -> None:
    rendered = _render_status_bar_for(state.value)
    others = [_render_status_bar_for(s.value) for s in ALL_OPERATION_STATES if s is not state]
    assert rendered not in others


@pytest.mark.parametrize("label", ALL_DISPLAY_LABELS, ids=_ids(ALL_DISPLAY_LABELS))
def test_integration_label_distinct_integration_label(label: IntegrationDisplayLabel) -> None:
    output = format_integration_label(label)
    others = [format_integration_label(v) for v in ALL_DISPLAY_LABELS if v is not label]
    assert output not in others


@pytest.mark.parametrize("label", ALL_DISPLAY_LABELS, ids=_ids(ALL_DISPLAY_LABELS))
def test_integration_label_distinct_auto_integrate_message(label: IntegrationDisplayLabel) -> None:
    """Each of the seven labels also routes distinctly through the live-line formatter."""
    message = format_auto_integrate_message(operation_display_state=label)
    others = [
        format_auto_integrate_message(operation_display_state=v)
        for v in ALL_DISPLAY_LABELS
        if v is not label
    ]
    assert message not in others


@pytest.mark.parametrize("label", ALL_DISPLAY_LABELS, ids=_ids(ALL_DISPLAY_LABELS))
def test_integration_label_distinct_status_bar(label: IntegrationDisplayLabel) -> None:
    rendered = _render_status_bar_for(label.value)
    others = [_render_status_bar_for(v.value) for v in ALL_DISPLAY_LABELS if v is not label]
    assert rendered not in others
