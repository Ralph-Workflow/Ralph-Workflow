"""AC-09: log-line tag markers in ParallelDisplay output are preserved byte-for-byte.

The PlainLogRenderer emits these verified log-line tag markers as substrings of
the rendered line:

  1. ``[run-start]`` \u2014 emitted by ``emit_run_start``
  2. ``[phase-close]`` \u2014 emitted by ``emit_phase_close``
  3. ``[run-end]`` \u2014 emitted by ``emit_run_end``
  4. ``[{tag}][{unit_id}]`` \u2014 emitted by ``emit`` (via ``emit_log_line``)

The markers ``[phase-start]`` and ``[unit-id]`` are NOT real markers and must
NOT be asserted. The ``[{unit_id}]`` substring is asserted via a substring
``in`` check on the supplied unit_id, not an exact-marker match.

The markers are byte-for-byte load-bearing for downstream log parsers; this
test is the AC-09 evidence that the visual hierarchy refactor in
wt-007-consolidate-display did NOT drop any tag markers.
"""

from __future__ import annotations

import io

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.plain_renderer import RunStartOrientation


def test_parallel_display_preserves_log_tags() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={"CI": "1"})
    pd = ParallelDisplay(ctx)

    pd.emit_run_start(RunStartOrientation())
    pd.emit_phase_close("planning", "artifacts")
    pd.emit_run_end(phase="final")
    pd.emit(unit_id="unit-1", line="test log line")

    text = buf.getvalue()
    assert "[run-start]" in text, (
        f"[run-start] marker missing from ParallelDisplay output:\n{text!r}"
    )
    assert "[phase-close]" in text, (
        f"[phase-close] marker missing from ParallelDisplay output:\n{text!r}"
    )
    assert "[run-end]" in text, (
        f"[run-end] marker missing from ParallelDisplay output:\n{text!r}"
    )
    assert "unit-1" in text, (
        f"unit_id 'unit-1' missing from ParallelDisplay output:\n{text!r}"
    )
