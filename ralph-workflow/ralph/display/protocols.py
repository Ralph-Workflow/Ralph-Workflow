"""Typed protocol interfaces for display components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rich.console import Console, RenderableType
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot
    from ralph.pipeline.state import PipelineState


class SubscriberProtocol(Protocol):
    """Receives pipeline state snapshots after each reducer reduce."""

    def notify(self, state: PipelineState) -> None: ...


class PanelRenderer(Protocol):
    """Renders a panel from a dashboard snapshot."""

    name: str

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = ...,
        width: int | None = None,
    ) -> RenderableType: ...


class LayoutSpec(Protocol):
    """Opaque layout specification selected for dashboard rendering."""


class LayoutSelector(Protocol):
    """Selects a layout spec based on snapshot and terminal width."""

    def __call__(
        self,
        snapshot: DashboardSnapshot,
        *,
        terminal_width: int,
    ) -> LayoutSpec: ...


class ModeSelectorProtocol(Protocol):
    """Selects dashboard vs lines mode."""

    def __call__(
        self,
        console: Console,
        env: Mapping[str, str],
    ) -> Literal["dashboard", "lines"]: ...


__all__ = [
    "LayoutSelector",
    "LayoutSpec",
    "ModeSelectorProtocol",
    "PanelRenderer",
    "SubscriberProtocol",
]
