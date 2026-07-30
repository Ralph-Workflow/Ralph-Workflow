from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.activity_model import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

if TYPE_CHECKING:
    from pathlib import Path


def test_emit_activity_event_accepts_missing_metadata(tmp_path: Path) -> None:
    """Streaming TEXT events buffer silently; close emits the joined passage.

    S-7 (wt-028-display P1): ``_emit_activity_event`` for a TEXT event
    buffers the fragment into ``_active_block`` and returns without
    printing. ``flush_blocks`` (or a non-streaming event) closes the
    block and emits the coalesced entry with the joined passage.
    """
    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    display = ParallelDisplay(
        make_display_context(console=console, env={}),
        workspace_root=tmp_path,
    )

    display._emit_activity_event("unit-1", ActivityEventKind.TEXT, "hello", None)
    display.flush_blocks()

    assert "hello" in console.export_text()
