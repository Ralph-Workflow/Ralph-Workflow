"""Tests for PrefixedLineRenderer."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.render_thread import UpdateEvent
from ralph.display.renderers.prefixed_lines import PrefixedLineRenderer


def test_no_ansi_in_non_color() -> None:
    """Test no ANSI escape codes are emitted when color system is None."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True)
    renderer = PrefixedLineRenderer(console)

    event = UpdateEvent(unit_id="u1", kind="output", payload="hello world")
    renderer.handle_event(event)

    output = buf.getvalue()
    assert "\x1b[" not in output


def test_prefix_on_output_event() -> None:
    """Test output events are prefixed with [unit_id]."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True)
    renderer = PrefixedLineRenderer(console)

    event = UpdateEvent(unit_id="test-unit", kind="output", payload="some output")
    renderer.handle_event(event)

    output = buf.getvalue()
    assert "[test-unit] some output" in output


def test_prefix_on_status_event() -> None:
    """Test status events are prefixed with [unit_id] STATUS:."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True)
    renderer = PrefixedLineRenderer(console)

    event = UpdateEvent(unit_id="unit-42", kind="status", payload="RUNNING")
    renderer.handle_event(event)

    output = buf.getvalue()
    assert "[unit-42] STATUS: RUNNING" in output


def test_multiple_units_interleaved() -> None:
    """Test multiple units' events each have correct prefix."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True)
    renderer = PrefixedLineRenderer(console)

    events = [
        UpdateEvent(unit_id="u1", kind="output", payload="line1"),
        UpdateEvent(unit_id="u2", kind="output", payload="line2"),
        UpdateEvent(unit_id="u3", kind="output", payload="line3"),
        UpdateEvent(unit_id="u1", kind="status", payload="DONE"),
        UpdateEvent(unit_id="u2", kind="status", payload="DONE"),
    ]

    for event in events:
        renderer.handle_event(event)

    output = buf.getvalue()
    lines = output.strip().split("\n")
    assert len(lines) == len(events)

    # Order-agnostic: check each unit's output appears with correct prefix
    assert any("[u1] line1" in line for line in lines)
    assert any("[u2] line2" in line for line in lines)
    assert any("[u3] line3" in line for line in lines)
    assert any("[u1] STATUS: DONE" in line for line in lines)
    assert any("[u2] STATUS: DONE" in line for line in lines)
