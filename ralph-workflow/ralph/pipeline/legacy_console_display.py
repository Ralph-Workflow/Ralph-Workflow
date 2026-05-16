"""Legacy console display and display helper utilities for the pipeline runner."""

from __future__ import annotations

import uuid
from importlib import import_module
from typing import TYPE_CHECKING, cast

from loguru import logger
from rich.text import Text

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.policy.models import PolicyBundle


def _parallel_display_cls() -> type[ParallelDisplay]:
    module = import_module("ralph.display.parallel_display")
    return cast("type[ParallelDisplay]", module.ParallelDisplay)


class LegacyConsoleDisplay:
    """Legacy console display that uses a caller-provided DisplayContext."""

    def __init__(self, display_context: DisplayContext) -> None:
        self._ctx = display_context

    @property
    def console(self) -> Console:
        return self._ctx.console

    def __enter__(self) -> LegacyConsoleDisplay:
        return self

    def __exit__(self, _exc_type: object, _exc_val: object, _exc_tb: object) -> None:
        return None

    def emit(self, unit_id: str | None, line: Text | str) -> None:
        c = self.console
        if unit_id is None:
            c.print(line)
            return
        c.print(f"[{unit_id}] {line}")


def display_console(
    display: ParallelDisplay | LegacyConsoleDisplay | None,
    display_context: DisplayContext | None = None,
) -> Console:
    if display is None:
        if display_context is None:
            raise TypeError("display_context is required when display is None")
        return display_context.console
    return display.console


def get_display_context(
    display: ParallelDisplay | LegacyConsoleDisplay | None,
    display_context: DisplayContext | None = None,
) -> DisplayContext:
    """Extract DisplayContext from display or use the caller-provided context."""
    if display is None:
        if display_context is None:
            raise TypeError("display_context is required when display is None")
        return display_context
    ctx = display._ctx
    assert ctx is not None
    return ctx


def emit_display_line(
    display: ParallelDisplay | LegacyConsoleDisplay | None,
    unit_id: str | None,
    line: Text | str,
    display_context: DisplayContext | None = None,
) -> None:
    if display is None:
        if display_context is None:
            raise TypeError("display_context is required when display is None")
        c = display_context.console
        if unit_id is None:
            c.print(line)
            return
        c.print(f"[{unit_id}] {line}")
        return
    if isinstance(display, LegacyConsoleDisplay):
        display.emit(unit_id, line)
        return
    content: str = line.plain if isinstance(line, Text) else line
    display.emit(unit_id or "run", content)


def resolve_display(
    display: ParallelDisplay | None,
    display_context: DisplayContext | None = None,
) -> ParallelDisplay | LegacyConsoleDisplay:
    if display is not None:
        return display
    if display_context is None:
        raise TypeError("display_context is required when display is None")
    return LegacyConsoleDisplay(display_context)


def subscriber_for_display(
    display: ParallelDisplay | LegacyConsoleDisplay | None,
) -> PipelineSubscriber | None:
    """Extract the pipeline subscriber from a display, when one is exposed."""
    if display is None or isinstance(display, LegacyConsoleDisplay):
        return None
    if not hasattr(display, "subscriber"):
        return None
    return cast("PipelineSubscriber | None", display.subscriber)


def status_text(label: str, value: str, style: str) -> Text:
    """Create a styled status text line."""
    text = Text()
    text.append(f"{label}: ", style=f"bold {style}")
    text.append(value, style=style)
    return text


def build_default_display(
    workspace_root: Path,
    display_context: DisplayContext,
    pipeline_policy: PolicyBundle | None = None,
) -> ParallelDisplay | LegacyConsoleDisplay:
    """Construct the default ParallelDisplay for the verbose run path.

    Falls back to the legacy console display if ParallelDisplay (or its
    transitive Rich/panel dependencies) cannot be imported or initialized.
    """
    try:
        parallel_display_cls = _parallel_display_cls()
        return parallel_display_cls(
            display_context,
            workspace_root=workspace_root,
            run_id=str(uuid.uuid4()),
            pipeline_policy=pipeline_policy.pipeline if pipeline_policy is not None else None,
        )
    except Exception:
        logger.debug(
            "ParallelDisplay unavailable or failed to initialize; falling back to legacy console",
            exc_info=True,
        )
        return LegacyConsoleDisplay(display_context)
