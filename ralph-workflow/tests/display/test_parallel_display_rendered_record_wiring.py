"""Black-box tests for the rendered-record production wiring.

P0 (wt-028-display S-11 / AC-07): a live run produces a rendered
record at ``.agent/raw/<safe_id>.rendered.log`` with one entry per
event, no ANSI color codes, and a stable field order. The writer
itself is exercised directly in
``tests/test_raw_overflow.py`` and
``tests/display/test_presented_entry_canonical.py``; this file
verifies the *production* seam - that ``ParallelDisplay`` constructs,
appends to, and flushes the writer during its own lifecycle.

Each test constructs a fresh ``ParallelDisplay`` with a StringIO
console and ``tmp_path`` workspace, drives a few events through the
canonical ``_emit_activity_event`` / ``emit_parsed_event`` paths,
stops the display, and asserts the writer's output file is on disk
with the expected shape. No real I/O, no subprocess, no wall-clock
sleep; the whole file finishes in < 0.5 s so the 60 s combined
budget is preserved.
"""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.record_writer import safe_id_for


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, io.StringIO]:
    """Build a ``ParallelDisplay`` rooted at ``tmp_path`` with a StringIO console."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    ctx = make_display_context(console=console, env={"CI": "1"})
    return ParallelDisplay(ctx, workspace_root=tmp_path), buf


def test_emit_parsed_event_writes_one_line_to_rendered_record(tmp_path: Path) -> None:
    """``emit_parsed_event`` appends exactly one line per event to the rendered record.

    The first ``emit_parsed_event`` for a unit creates the writer
    lazily; after ``stop()``, ``.agent/raw/<safe_id>.rendered.log``
    contains the same single line.
    """
    pd, _buf = _make_display(tmp_path)
    pd.emit_parsed_event(
        unit_id="claude",
        kind=ActivityEventKind.TEXT,
        content="Locating where the elapsed time is recomputed.",
        metadata={},
    )
    pd.stop()
    expected_path = tmp_path / ".agent" / "raw" / f"{safe_id_for('claude')}.rendered.log"
    assert expected_path.exists(), f"rendered record missing at {expected_path}"
    body = expected_path.read_text(encoding="utf-8")
    # Stable field order: timestamp, agent, severity, body. The body
    # contains the unit's text content exactly once (no duplication).
    assert body.count("Locating where the elapsed time is recomputed.") == 1
    assert "agent=claude" in body
    # No ANSI escape codes: the rendered record is text-first, not
    # the live colored surface.
    assert "\x1b[" not in body


def test_emit_parsed_event_writes_one_line_per_event(tmp_path: Path) -> None:
    """N events produce N lines in the rendered record (one entry per event).

    This is the AC-07 invariant: the same logical event is rendered
    once. The live log and the rendered record both carry exactly
    one entry per event - preview-then-repeat and
    preview-then-summary duplication is gone from the rendered
    surface.
    """
    pd, _buf = _make_display(tmp_path)
    for index in range(5):
        pd.emit_parsed_event(
            unit_id="codex",
            kind=ActivityEventKind.TEXT,
            content=f"event {index} body",
            metadata={},
        )
    pd.stop()
    expected_path = tmp_path / ".agent" / "raw" / f"{safe_id_for('codex')}.rendered.log"
    body = expected_path.read_text(encoding="utf-8")
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(lines) == 5, f"expected 5 lines, got {len(lines)}: {lines!r}"
    for index in range(5):
        assert f"event {index} body" in body


def test_quiet_mode_does_not_open_rendered_writer(tmp_path: Path) -> None:
    """Quiet mode skips the writer entirely so single-line runs pay nothing."""
    _pd, _buf = _make_display(tmp_path)
    pd_quiet = ParallelDisplay(
        make_display_context(
            console=Console(file=io.StringIO(), force_terminal=False, color_system=None, width=120),
            env={"CI": "1"},
        ),
        workspace_root=tmp_path,
        is_quiet=True,
    )
    pd_quiet.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TEXT,
        content="quiet event",
        metadata={},
    )
    pd_quiet.stop()
    # The quiet display never called ``_get_rendered_writer``, so the
    # .agent/raw directory is either absent or contains no .rendered.log
    # file for this unit.
    expected = tmp_path / ".agent" / "raw" / f"{safe_id_for('pi')}.rendered.log"
    assert not expected.exists()


def test_drop_unit_flushes_rendered_writer(tmp_path: Path) -> None:
    """``drop_unit`` flushes the writer before discarding it."""
    pd, _buf = _make_display(tmp_path)
    pd.emit_parsed_event(
        unit_id="claude",
        kind=ActivityEventKind.TEXT,
        content="before drop",
        metadata={},
    )
    # ``stop()`` was not called; ``drop_unit`` should still flush.
    pd.drop_unit("claude")
    expected_path = tmp_path / ".agent" / "raw" / f"{safe_id_for('claude')}.rendered.log"
    assert expected_path.exists()
    body = expected_path.read_text(encoding="utf-8")
    assert "before drop" in body
    # The writer is removed from the per-unit cache so a second
    # ``drop_unit`` is a no-op.
    pd.drop_unit("claude")
