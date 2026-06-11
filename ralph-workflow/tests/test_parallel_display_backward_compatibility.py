from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.activity_model import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

if TYPE_CHECKING:
    from pathlib import Path


def test_emit_activity_event_accepts_missing_metadata(tmp_path: Path) -> None:
    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    display = ParallelDisplay(
        make_display_context(console=console, env={}),
        workspace_root=tmp_path,
    )

    display._emit_activity_event("unit-1", ActivityEventKind.TEXT, "hello", None)

    assert "hello" in console.export_text()
