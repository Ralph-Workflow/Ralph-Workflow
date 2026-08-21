"""The run's cleanup step must flush the display's buffered writers.

A serial ``ralph run`` ends with neither ``drop_unit`` (parallel-only)
nor ``ParallelDisplay.stop()`` -- the cleanup step named "display stop"
invokes the width refresher's stop, not the display's. So the buffered
per-unit writers were never flushed on the primary run path:
``RenderedRecordWriter`` buffers in memory with no atexit or finalizer,
and the condensation markers advertised a condensed log by path that
could still be empty.

This pins the wiring at the seam the run loop actually calls, so a
refactor of ``_setup_active_display`` cannot silently drop it again.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from ralph.display.activity_model import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.raw_overflow import raw_log_path_for
from ralph.pipeline.run_loop import compose_display_stop

pytestmark = pytest.mark.timeout_seconds(5)

_OVERSIZED_BODY = "---\ntype: development_result\n---\n" + "\n".join(
    f"- [SUM-{n}] body line" for n in range(400)
)


def test_composed_display_stop_flushes_buffered_writers(tmp_path: Path) -> None:
    """The cleanup callable the run loop installs must reach the display."""
    console = Console(file=io.StringIO(), force_terminal=False, color_system=None, width=200)
    display = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    unit_id = "unit-flush"
    refresher_calls: list[int] = []
    display_stop = compose_display_stop(lambda: refresher_calls.append(1), display)

    display.emit_parsed_event(unit_id, ActivityEventKind.TOOL_RESULT, _OVERSIZED_BODY, {})
    condensed = raw_log_path_for(tmp_path, unit_id, condensed=True)
    assert condensed.stat().st_size == 0, "precondition: the body is still buffered"

    display_stop()

    assert refresher_calls == [1], "the composed callable must still stop the refresher"
    assert condensed.stat().st_size > 0, "cleanup left the advertised file empty"


def test_composed_display_stop_flushes_even_if_the_refresher_raises(
    tmp_path: Path,
) -> None:
    """A refresher failure must not cost the run its buffered output."""
    console = Console(file=io.StringIO(), force_terminal=False, color_system=None, width=200)
    display = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )

    unit_id = "unit-raise"

    def _raising_stop() -> None:
        raise RuntimeError("refresher failed")

    display_stop = compose_display_stop(_raising_stop, display)
    display.emit_parsed_event(unit_id, ActivityEventKind.TOOL_RESULT, _OVERSIZED_BODY, {})

    with pytest.raises(RuntimeError):
        display_stop()

    assert raw_log_path_for(tmp_path, unit_id, condensed=True).stat().st_size > 0


def test_run_end_flush_is_idempotent(tmp_path: Path) -> None:
    """Cleanup can run more than once; it must not raise or double-write."""
    console = Console(file=io.StringIO(), force_terminal=False, color_system=None, width=200)
    display = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    unit_id = "unit-idempotent"
    display.emit_parsed_event(unit_id, ActivityEventKind.TOOL_RESULT, _OVERSIZED_BODY, {})

    display.flush_run_end_writers()
    display.flush_run_end_writers()

    body = raw_log_path_for(tmp_path, unit_id, condensed=True).read_text(encoding="utf-8")
    assert body.count("type: development_result") == 1, "the body was written twice"
