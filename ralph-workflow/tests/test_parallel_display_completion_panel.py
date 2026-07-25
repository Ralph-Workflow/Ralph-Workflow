"""Tests for ParallelDisplay emit_run_end: [run-end] block wiring only.

Completion panels are now emitted by _emit_final_summary in runner.py,
not by emit_run_end.  These tests verify the [run-end] block behaviour
without asserting that the completion panel appears here.
"""

from __future__ import annotations

from pathlib import Path
from queue import Queue

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.subscriber import PipelineSubscriber
from ralph.display.theme import RALPH_THEME
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULT_POLICY = load_policy(Path(__file__).parent.parent / "ralph" / "policy" / "defaults")


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, Console]:
    console = Console(
        record=True,
        width=120,
        force_terminal=False,
        color_system=None,
        highlight=False,
    )
    snapshot_q: Queue = Queue(maxsize=64)
    subscriber = PipelineSubscriber(
        queue=snapshot_q,
        workspace_root=tmp_path,
        run_id="test-run",
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )
    display = ParallelDisplay(
        make_display_context(console=console, env={}),
        workspace_root=tmp_path,
        subscriber=subscriber,
    )
    return display, console


def test_emit_run_end_complete_state_emits_run_end_block_only(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    state = PipelineState(phase="complete")
    display.subscriber.notify(state)
    display.emit_run_end(phase="complete", total_agent_calls=1)
    out = console.export_text()
    assert "[run-end]" in out
    # Completion panel is emitted by _emit_final_summary, not here
    assert "Pipeline Complete" not in out


def test_emit_run_end_without_last_state_still_emits_run_end_lines(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    display.emit_run_end(phase="complete", total_agent_calls=0)
    out = console.export_text()
    assert "[run-end]" in out
    assert "◆ Ralph Workflow run end" in out


def test_emit_run_end_failed_state_emits_run_end_block_only(tmp_path: Path) -> None:
    failure_phase = _DEFAULT_POLICY.pipeline.recovery.failed_route
    display, console = _make_display(tmp_path)
    state = PipelineState(phase=failure_phase, last_error="something broke")
    display.subscriber.notify(state)
    display.emit_run_end(phase=failure_phase, total_agent_calls=0)
    out = console.export_text()
    assert "[run-end]" in out
    # Completion panel is emitted by _emit_final_summary, not here
    assert "Pipeline Failed" not in out


def test_emit_run_end_non_terminal_phase_no_panel(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    state = PipelineState(phase="planning")
    display.subscriber.notify(state)
    display.emit_run_end(phase="planning", total_agent_calls=0)
    out = console.export_text()
    assert "Pipeline Complete" not in out
    assert "Pipeline Failed" not in out
    assert "[run-end]" in out


# --- wt-028-display S-6 / AC-05 / DA-005: height-constrained panel
# degradation. The shared threshold (12 rows) is honored by every
# Panel-using emit method: at the canonical 12-row floor and below,
# the bordered Panel degrades to unboxed headed text; above the
# floor (height=13+) the full Panel survives. The tests below pin
# the degradation for ``emit_info_panel``, ``emit_welcome_banner``,
# and ``emit_first_run_panel`` so a regression that re-introduces
# a border on a short terminal fails. The threshold check is
# at-or-below (``<=``) so the canonical floor (a 12-row split
# pane -- the documented accessibility path for large-text /
# magnified / braille displays) activates the constrained
# presentation.
# -------------------------------------------------------------------------


def _make_height_aware_display(
    tmp_path: Path, *, height: int
) -> tuple[ParallelDisplay, Console]:
    """Build a display whose Console carries the requested ``height``.

    The :class:`rich.console.Console` is created with
    ``record=True`` so :meth:`export_text` captures the rendered
    output, and with ``theme=RALPH_THEME`` so the
    ``theme.banner.border`` / ``theme.phase.planning`` styles
    the panel renderables reference resolve to a real color
    (without the theme, ``Panel(border_style=...)`` raises
    ``MissingStyle`` and the panel is silently dropped by the
    ``contextlib.suppress(Exception)`` wrapper in the emit
    method). ``force_terminal=False`` matches the
    ``_make_display`` helper used by the existing tests in this
    file (a record-mode Console with ``force_terminal=True``
    captures inconsistently when the Panel renderable is
    involved; the canonical pattern is ``record=True`` +
    ``force_terminal=False`` so the record is a pure plain-text
    capture without the Live / is_terminal branches).
    """
    console = Console(
        record=True,
        width=120,
        height=height,
        force_terminal=False,
        color_system=None,
        highlight=False,
        theme=RALPH_THEME,
    )
    snapshot_q: Queue = Queue(maxsize=64)
    subscriber = PipelineSubscriber(
        queue=snapshot_q,
        workspace_root=tmp_path,
        run_id=f"test-run-h{height}",
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )
    display = ParallelDisplay(
        make_display_context(console=console, env={}),
        workspace_root=tmp_path,
        subscriber=subscriber,
    )
    return display, console


def test_emit_info_panel_degrades_to_unboxed_at_11_rows(tmp_path: Path) -> None:
    """S-6: at height=11 the bordered info Panel becomes unboxed heading + body."""
    display, console = _make_height_aware_display(tmp_path, height=11)
    display.emit_info_panel(title="Next steps", content="Run ralph --init to bootstrap.")
    out = console.export_text()
    # Title is present (as a heading), the body is present.
    assert "Next steps" in out, f"unboxed title missing at 11 rows:\n{out!r}"
    assert "Run ralph --init to bootstrap." in out, f"unboxed body missing at 11 rows:\n{out!r}"
    # The full boxed Panel would draw a top/bottom border made of
    # ─ or ╭╮╰╯ Unicode box-drawing characters. The unboxed
    # heading has none of those around the title line.
    # Specifically, the title line ("Next steps") is NOT flanked
    # by box-drawing characters on the same line.
    next_steps_lines = [
        line for line in out.splitlines() if "Next steps" in line
    ]
    assert next_steps_lines, f"title line not found:\n{out!r}"
    title_line = next_steps_lines[0]
    for box_char in ("╭", "╮", "╰", "╯", "┌", "┐", "└", "┘", "─"):
        assert box_char not in title_line, (
            f"boxed title line found at 11 rows: {title_line!r}"
        )


def test_emit_info_panel_degrades_at_12_rows(tmp_path: Path) -> None:
    """S-6 / DA-005: at the canonical 12-row floor the Panel degrades to unboxed.

    The canonical 12-row floor is the documented accessibility
    path (large-text / magnified / braille displays); the framed
    presentation must give way to unboxed headed text there, not
    one row later.
    """
    display, console = _make_height_aware_display(tmp_path, height=12)
    display.emit_info_panel(title="Next steps", content="Run ralph --init to bootstrap.")
    out = console.export_text()
    assert "Next steps" in out
    assert "Run ralph --init to bootstrap." in out
    # The unboxed heading uses a plain ``Rule`` (filled with
    # horizontal box-drawing characters) but no panel corners. The
    # boxed form carries panel corner characters at the title.
    for corner in ("╭", "╮", "╰", "╯", "┌", "┐", "└", "┘"):
        assert corner not in out, (
            f"boxed Panel corner character {corner!r} survived at 12 rows (the floor):\n{out!r}"
        )


def test_emit_info_panel_keeps_box_at_24_rows(tmp_path: Path) -> None:
    """S-6: at height=24 the full boxed info Panel is preserved."""
    display, console = _make_height_aware_display(tmp_path, height=24)
    display.emit_info_panel(title="Next steps", content="Run ralph --init to bootstrap.")
    out = console.export_text()
    assert "Next steps" in out
    assert "Run ralph --init to bootstrap." in out
    has_box = any(
        char in out
        for char in ("╭", "╮", "╰", "╯", "┌", "┐", "└", "┘", "─", "│")
    )
    assert has_box, f"boxed Panel border missing at 24 rows:\n{out!r}"


def test_emit_welcome_banner_degrades_to_heading_at_11_rows(tmp_path: Path) -> None:
    """S-6: at height=11 the welcome banner is unboxed heading + 2 text lines."""
    display, console = _make_height_aware_display(tmp_path, height=11)
    display.emit_welcome_banner(version="9.9.9")
    out = console.export_text()
    # The heading + version line is present.
    assert "Ralph Workflow" in out, f"unboxed banner missing at 11 rows:\n{out!r}"
    assert "v9.9.9" in out, f"version missing at 11 rows:\n{out!r}"
    # The ASCII-art banner (the 6-row ``_ASCII_ART_BANNER``
    # block with pipe characters at the column edges) is NOT
    # rendered at 11 rows; the unboxed banner replaces it with a
    # single heading line + welcome + tagline.
    # Check that no line carries the pipe-rail ASCII art pattern.
    ascii_art_lines = [
        line for line in out.splitlines()
        if line.lstrip().startswith("\u2502") and line.rstrip().endswith("\u2502")
    ]
    assert not ascii_art_lines, (
        f"boxed ASCII-art banner survived at 11 rows: {ascii_art_lines!r}"
    )


def test_emit_welcome_banner_degrades_at_12_rows(tmp_path: Path) -> None:
    """S-6 / DA-005: at the canonical 12-row floor the welcome banner degrades.

    The canonical 12-row floor is the documented accessibility
    path (large-text / magnified / braille displays); the framed
    banner must give way to unboxed headed text there.
    """
    display, console = _make_height_aware_display(tmp_path, height=12)
    display.emit_welcome_banner(version="9.9.9")
    out = console.export_text()
    # Heading + version line still present (information preserved).
    assert "Ralph Workflow" in out, f"unboxed banner missing at 12 rows:\n{out!r}"
    assert "v9.9.9" in out, f"version missing at 12 rows:\n{out!r}"
    # The ASCII-art banner (the 6-row ``_ASCII_ART_BANNER`` block
    # with pipe characters at the column edges) is NOT rendered at
    # the canonical floor; the unboxed banner replaces it with a
    # single heading line.
    ascii_art_lines = [
        line for line in out.splitlines()
        if line.lstrip().startswith("\u2502") and line.rstrip().endswith("\u2502")
    ]
    assert not ascii_art_lines, (
        f"boxed ASCII-art banner survived at 12 rows (the floor): {ascii_art_lines!r}"
    )


def test_emit_first_run_panel_degrades_to_unboxed_at_11_rows(tmp_path: Path) -> None:
    """S-6: at height=11 the first-run Panel becomes unboxed heading + content."""
    from rich.text import Text

    display, console = _make_height_aware_display(tmp_path, height=11)
    display.emit_first_run_panel(content=[Text("body line 1"), Text("body line 2")])
    out = console.export_text()
    # The unboxed heading is present (the section rule is emitted
    # first, then the title rule, then the body lines).
    assert "Ralph Workflow first-run setup" in out, (
        f"unboxed first-run heading missing at 11 rows:\n{out!r}"
    )
    assert "body line 1" in out
    assert "body line 2" in out
    # The full boxed Panel draws a top/bottom border made of
    # corner box-drawing characters (``╭``/``╮``/``╰``/``╯``).
    # The unboxed heading uses ``console.rule()`` which fills
    # with the horizontal ``─`` character but does NOT have
    # corners. The corner check distinguishes the two: the boxed
    # form has ``╭`` / ``╯`` somewhere in the output, the
    # unboxed form has only ``─``.
    for corner in ("╭", "╮", "╰", "╯", "┌", "┐", "└", "┘"):
        assert corner not in out, (
            f"boxed Panel corner character {corner!r} survived at 11 rows:\n{out!r}"
        )


def test_emit_first_run_panel_degrades_at_12_rows(tmp_path: Path) -> None:
    """S-6 / DA-005: at the canonical 12-row floor the first-run Panel degrades.

    The canonical 12-row floor is the documented accessibility
    path (large-text / magnified / braille displays); the framed
    Panel must give way to unboxed headed text there.
    """
    from rich.text import Text

    display, console = _make_height_aware_display(tmp_path, height=12)
    display.emit_first_run_panel(content=[Text("body line 1")])
    out = console.export_text()
    assert "Ralph Workflow first-run setup" in out
    assert "body line 1" in out
    # The unboxed heading uses a plain ``Rule`` (filled with
    # horizontal box-drawing characters) but no panel corners.
    for corner in ("╭", "╮", "╰", "╯", "┌", "┐", "└", "┘"):
        assert corner not in out, (
            f"boxed Panel corner character {corner!r} survived at 12 rows (the floor):\n{out!r}"
        )
