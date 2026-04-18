"""Prefixed line renderer for non-TTY display mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

    from ralph.display.render_thread import UpdateEvent


class PrefixedLineRenderer:
    def __init__(self, console: Console) -> None:
        self._console = console

    def handle_event(self, event: UpdateEvent) -> None:
        if event.kind == "output":
            self._write_output(event)
        elif event.kind == "status":
            self._write_status(event)

    def _write_output(self, event: UpdateEvent) -> None:
        prefix = f"[{event.unit_id}] " if event.unit_id else ""
        self._console.out(f"{prefix}{event.payload}")

    def _write_status(self, event: UpdateEvent) -> None:
        prefix = f"[{event.unit_id}] " if event.unit_id else ""
        self._console.out(f"{prefix}STATUS: {event.payload}")
