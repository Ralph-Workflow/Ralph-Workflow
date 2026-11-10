"""Tests for status bar and parallel display status line rendering.

Verifies that:
1. Status text renders resolution failure cause and fix once, wrapped and untruncated.
2. Status bar formatting does not hard-truncate the cause and fix ("fix <file> then run ralph --check-config"), wrapping cleanly.
3. Repeated renders while in waiting/resolution failure add no redundant log lines.
"""

from __future__ import annotations

import io

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay, status_text
from ralph.display.status_bar import StatusBar, StatusBarModel, render_status_bar


def _make_console(width: int = 80, *, is_terminal: bool = False) -> tuple[Console, io.StringIO]:
    stream = io.StringIO()
    console = Console(file=stream, width=width, force_terminal=is_terminal, color_system=None)
    return console, stream


def test_waiting_status_text_preserves_cause_and_fix() -> None:
    """status_text preserves the resolution failure cause and fix untruncated."""
    reason = "fix /home/user/.config/ralph-workflow.toml then run ralph --check-config"
    value = f"integration conflict ({reason}) — resolution required"
    rendered = status_text("WAITING", value, "yellow")

    assert reason in rendered
    assert rendered.startswith("WAITING: ")
    assert "then run ralph --check-config" in rendered


def test_waiting_status_text_wraps_when_width_constrained() -> None:
    """status_text wraps cleanly with hanging indent when width is constrained."""
    reason = "fix /home/user/.config/ralph-workflow.toml then run ralph --check-config"
    value = f"integration conflict ({reason}) — resolution required"
    rendered = status_text("WAITING", value, "yellow", width=80)

    lines = rendered.split("\n")
    assert len(lines) > 1, f"Expected wrapped multi-line output; got: {rendered!r}"
    for line in lines:
        assert len(line) <= 80, f"Line exceeds width 80: {line!r} (len={len(line)})"
    assert "/home/user/.config/ralph-workflow.toml" in rendered
    assert "--check-config" in rendered
    # Verify words are preserved when normalized for wrapping
    normalized = " ".join(rendered.split())
    assert reason in normalized


def test_waiting_status_bar_model_renders_cause_and_fix_untruncated_and_wrapped() -> None:
    """StatusBarModel formatting does not hard-truncate the cause and fix; wraps cleanly."""
    console, _ = _make_console(width=80, is_terminal=True)
    ctx = make_display_context(console=console, env={})
    cause_and_fix = "fix /home/user/.config/ralph-workflow.toml then run ralph --check-config"
    alert = f"integration conflict ({cause_and_fix}) — resolution required"

    model = StatusBarModel(
        workspace_root="/home/user/project",
        phase_label="auto-integrate",
        phase_style="theme.phase.development",
        attention="waiting",
        integration_alert=alert,
    )

    rendered = render_status_bar(model, ctx, home="/home/user")
    plain = rendered.plain

    # Verify cause and fix is untruncated
    assert "fix /home/user/.config/ralph-workflow.toml" in plain
    assert "then run ralph --check-config" in plain
    assert "resolution required" in plain

    # Verify every individual line fits within width budget
    for line in plain.split("\n"):
        assert len(line) <= 80, f"Rendered line exceeds terminal width: {line!r} (len={len(line)})"


def test_waiting_status_bar_repeated_renders_add_no_log_line() -> None:
    """Repeated StatusBar updates while waiting add no duplicate log lines."""
    console, stream = _make_console(width=100, is_terminal=False)
    ctx = make_display_context(console=console, env={})
    display = ParallelDisplay(ctx)
    status_bar = StatusBar(display)

    cause_and_fix = "fix /home/user/.config/ralph-workflow.toml then run ralph --check-config"
    alert = f"integration conflict ({cause_and_fix}) — resolution required"

    model_tick1 = StatusBarModel(
        workspace_root="/home/user/project",
        phase_label="auto-integrate",
        phase_style="theme.phase.development",
        attention="waiting",
        integration_alert=alert,
        elapsed_seconds=1.0,
    )
    status_bar.update(model_tick1)
    output_after_first = stream.getvalue()
    assert len(output_after_first) > 0
    assert "/home/user/.config/ralph-workflow.toml" in output_after_first
    assert "--check-config" in output_after_first

    # Simulate subsequent timer ticks while waiting for user to fix file
    for elapsed in [2.0, 3.0, 4.0, 5.0]:
        model_tick_n = StatusBarModel(
            workspace_root="/home/user/project",
            phase_label="auto-integrate",
            phase_style="theme.phase.development",
            attention="waiting",
            integration_alert=alert,
            elapsed_seconds=elapsed,
        )
        status_bar.update(model_tick_n)

    # Output must not have grown from elapsed seconds ticking during waiting
    assert stream.getvalue() == output_after_first


def test_waiting_status_bar_emits_when_cause_changes() -> None:
    """StatusBar emits a new line when the underlying failure cause or alert changes."""
    console, stream = _make_console(width=120, is_terminal=False)
    ctx = make_display_context(console=console, env={})
    display = ParallelDisplay(ctx)
    status_bar = StatusBar(display)

    model1 = StatusBarModel(
        workspace_root="/home/user/project",
        phase_label="auto-integrate",
        phase_style="theme.phase.development",
        attention="waiting",
        integration_alert="integration conflict (fix file1.toml then run ralph --check-config) — resolution required",
        elapsed_seconds=1.0,
    )
    status_bar.update(model1)
    output1 = stream.getvalue()
    assert "file1.toml" in output1

    model2 = StatusBarModel(
        workspace_root="/home/user/project",
        phase_label="auto-integrate",
        phase_style="theme.phase.development",
        attention="waiting",
        integration_alert="integration conflict (fix file2.toml then run ralph --check-config) — resolution required",
        elapsed_seconds=2.0,
    )
    status_bar.update(model2)
    output2 = stream.getvalue()
    assert "file2.toml" in output2
    assert output2 != output1


def test_waiting_parallel_display_repeated_status_emits_add_no_log_line() -> None:
    """ParallelDisplay.emit and emit_status_line deduplicate repeated WAITING status lines."""
    console, stream = _make_console(width=100, is_terminal=False)
    ctx = make_display_context(console=console, env={})
    display = ParallelDisplay(ctx)

    line = status_text(
        "WAITING",
        "integration conflict (fix /home/user/.config/ralph-workflow.toml then run ralph --check-config) — resolution required",
        "yellow",
    )

    display.emit(None, line)
    output_after_first = stream.getvalue()
    assert len(output_after_first) > 0
    assert "then run ralph --check-config" in output_after_first

    # Repeated emits of the same waiting status line should be no-ops
    for _ in range(5):
        display.emit(None, line)

    assert stream.getvalue() == output_after_first

    # Now test emit_status_line deduplication
    stream.truncate(0)
    stream.seek(0)
    display.emit_status_line("run", line)
    status_output1 = stream.getvalue()
    assert len(status_output1) > 0

    for _ in range(5):
        display.emit_status_line("run", line)

    assert stream.getvalue() == status_output1


def test_waiting_parallel_display_emit_renders_cause_and_fix_wrapped_and_untruncated() -> None:
    """ParallelDisplay.emit renders long cause and fix lines wrapped, not truncated."""
    console, stream = _make_console(width=80, is_terminal=False)
    ctx = make_display_context(console=console, env={})
    display = ParallelDisplay(ctx)

    reason = (
        "fix /home/user/.config/ralph-workflow-long-filename.toml then run ralph --check-config"
    )
    line = status_text(
        "WAITING", f"integration conflict ({reason}) — resolution required", "yellow"
    )

    display.emit(None, line)
    output = stream.getvalue()

    # The cause and fix must be completely present
    assert "/home/user/.config/ralph-workflow-long-filename.toml" in output
    assert "--check-config" in output
