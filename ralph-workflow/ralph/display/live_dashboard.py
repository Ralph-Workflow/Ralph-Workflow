"""Live dashboard: Rich Layout + Live composition with adaptive panel selection.

Mode is fixed at construction. Resize during run is tolerated by Rich's
``Layout`` overflow handling, but the chosen panel set does NOT swap to plain
mode mid-run.
"""

from __future__ import annotations

import queue
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel

from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from rich.console import Console, RenderableType
    from rich.theme import Theme

    from ralph.display.protocols import LayoutSelector, PanelRenderer
    from ralph.display.ring_buffer import RingBuffer
    from ralph.display.snapshot import DashboardSnapshot

type _SignalHandler = Callable[[int, object], None] | int | None

_MAX_GRID_WORKERS = 4

REGIONS: tuple[str, ...] = (
    "header",
    "plan",
    "phase_tracker",
    "progress",
    "workers",
    "log_tail",
    "results",
    "footer",
)


@dataclass(frozen=True, slots=True)
class LayoutSpec:
    """Immutable layout specification: ordered panel regions with sizing hints.

    Attributes:
        name: Identifier for this spec (e.g. ``"adaptive-grid"``).
        regions: Ordered ``(panel_name, size, ratio)`` tuples.
            *size* is a fixed row count or ``None``.
            *ratio* is a proportional weight or ``None``.
            If both are ``None``, Rich allocates space proportionally.
    """

    name: str
    regions: tuple[tuple[str, int | None, int | None], ...]


DEFAULT_LAYOUT_GRID = LayoutSpec(
    name="adaptive-grid",
    regions=(
        ("header",        3,    None),
        ("plan",          5,    None),
        ("phase_tracker", 4,    None),
        ("progress",      4,    None),
        ("worker_grid",   None, 2),
        ("log_tail",      None, 1),
        ("results",       4,    None),
        ("footer",        3,    None),
    ),
)

DEFAULT_LAYOUT_LIST = LayoutSpec(
    name="adaptive-list",
    regions=(
        ("header",        3,    None),
        ("plan",          4,    None),
        ("phase_tracker", 3,    None),
        ("progress",      3,    None),
        ("worker_list",   None, 3),
        ("log_tail",      None, 1),
        ("results",       3,    None),
        ("footer",        3,    None),
    ),
)


def _noop_sigwinch(signum: int, frame: object) -> None:
    pass


class AdaptiveLayoutSelector:
    def __call__(
        self,
        snapshot: DashboardSnapshot,
        *,
        terminal_width: int,
    ) -> LayoutSpec:
        if len(snapshot.workers) <= _MAX_GRID_WORKERS:
            return DEFAULT_LAYOUT_GRID
        return DEFAULT_LAYOUT_LIST


class _SnapshotRenderThread(threading.Thread):
    def __init__(
        self,
        snapshot_queue: queue.Queue[DashboardSnapshot],
        live: Live,
        set_snapshot: Callable[[DashboardSnapshot], None],
        render_fn: Callable[[], RenderableType],
        refresh_hz: int = 4,
    ) -> None:
        super().__init__(daemon=True, name="live-dashboard-render")
        self._queue = snapshot_queue
        self._live = live
        self._set_snapshot = set_snapshot
        self._render_fn = render_fn
        self._stop_event = threading.Event()
        self._refresh_hz = refresh_hz

    def run(self) -> None:
        while not self._stop_event.is_set():
            latest: DashboardSnapshot | None = None
            # Block briefly for the first snapshot, then drain any extras.
            try:
                snap = self._queue.get(timeout=0.05)
                self._queue.task_done()
                latest = snap
                while True:
                    try:
                        snap = self._queue.get_nowait()
                        self._queue.task_done()
                        latest = snap
                    except queue.Empty:
                        break
            except queue.Empty:
                pass

            if latest is not None:
                self._set_snapshot(latest)

            self._live.update(self._render_fn(), refresh=True)
            self._stop_event.wait(1 / self._refresh_hz)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=2.0)


class LiveDashboard:
    """Rich Live dashboard composing Layout regions from panel singletons.

    Receives :class:`~ralph.display.snapshot.DashboardSnapshot` objects via
    *snapshot_queue* and repaints all Layout regions at *refresh_per_second* Hz.

    The snapshot bridge contract:

    * ``DashboardSubscriber`` (T21) puts snapshots into *snapshot_queue* via
      ``put_nowait``.
    * ``LiveDashboard`` is constructed with that **same** queue.
    * The internal ``_SnapshotRenderThread`` drains the queue and re-renders
      all Layout regions from the latest snapshot.
    * There is **no** ``LiveDashboard.update(snapshot)`` public method — the
      queue is the sole input channel.  The ``update()`` helper provided here
      is a thin ``put_nowait`` wrapper kept for test ergonomics only.

    Usage::

        dash = LiveDashboard(console=console, panels=panels, ...)
        with dash:
            # DashboardSubscriber drives updates via the shared queue
            ...
    """

    __slots__ = (
        "_buffers",
        "_clock",
        "_console",
        "_latest_snapshot",
        "_layout_selector",
        "_live",
        "_panel_map",
        "_prev_sigwinch",
        "_refresh_per_second",
        "_render_thread",
        "_snapshot_queue",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        console: Console,
        panels: tuple[PanelRenderer, ...],
        buffers: Mapping[str, RingBuffer],
        snapshot_queue: queue.Queue[DashboardSnapshot],
        layout_selector: LayoutSelector = AdaptiveLayoutSelector(),  # noqa: B008
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        refresh_per_second: int = 4,
    ) -> None:
        self._console = console
        self._panel_map: dict[str, PanelRenderer] = {p.name: p for p in panels}
        self._buffers = buffers
        self._snapshot_queue = snapshot_queue
        self._layout_selector = layout_selector
        self._clock = clock
        self._refresh_per_second = refresh_per_second
        self._latest_snapshot: DashboardSnapshot | None = None
        self._live: Live | None = None
        self._render_thread: _SnapshotRenderThread | None = None
        self._prev_sigwinch: _SignalHandler = None

        # Validate default specs at construction (EA-8 extensibility safety net).
        for spec in (DEFAULT_LAYOUT_GRID, DEFAULT_LAYOUT_LIST):
            for region_name, _, _ in spec.regions:
                if region_name not in self._panel_map:
                    raise KeyError(
                        f"Panel {region_name!r} referenced in LayoutSpec"
                        f" {spec.name!r} not found in panels tuple"
                    )

    def _set_latest_snapshot(self, snapshot: DashboardSnapshot) -> None:
        self._latest_snapshot = snapshot

    def _render_once(self) -> RenderableType:
        snap = self._latest_snapshot
        if snap is None:
            return Panel(
                "[dim]Starting…[/dim]",
                title="Ralph Workflow",
                border_style="dim",
            )

        spec = cast("LayoutSpec", self._layout_selector(snap, terminal_width=self._console.width))
        layout = Layout()

        sub_layouts: list[Layout] = []
        for region_name, size, ratio in spec.regions:
            if size is not None:
                sub_layouts.append(Layout(name=region_name, size=size))
            elif ratio is not None:
                sub_layouts.append(Layout(name=region_name, ratio=ratio))
            else:
                sub_layouts.append(Layout(name=region_name))
        layout.split_column(*sub_layouts)

        theme: Theme = RALPH_THEME
        for region_name, _, _ in spec.regions:
            renderer = self._panel_map.get(region_name)
            if renderer is None:
                continue
            rendered = renderer.render(snap, theme=theme)
            layout[region_name].update(rendered)

        return layout

    def update(self, snapshot: DashboardSnapshot) -> None:
        self._snapshot_queue.put_nowait(snapshot)

    def __enter__(self) -> LiveDashboard:
        live = Live(
            self._render_once(),
            console=self._console,
            refresh_per_second=self._refresh_per_second,
            vertical_overflow="ellipsis",
            auto_refresh=True,
        )
        live.start()
        self._live = live

        self._render_thread = _SnapshotRenderThread(
            snapshot_queue=self._snapshot_queue,
            live=live,
            set_snapshot=self._set_latest_snapshot,
            render_fn=self._render_once,
            refresh_hz=self._refresh_per_second,
        )
        self._render_thread.start()

        if hasattr(signal, "SIGWINCH"):
            self._prev_sigwinch = cast(
                "_SignalHandler", signal.signal(signal.SIGWINCH, _noop_sigwinch)
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._render_thread is not None:
            self._render_thread.stop()
            self._render_thread = None
        if self._live is not None:
            self._live.stop()
            self._live = None
        if hasattr(signal, "SIGWINCH") and self._prev_sigwinch is not None:
            signal.signal(signal.SIGWINCH, self._prev_sigwinch)
            self._prev_sigwinch = None


__all__ = [
    "DEFAULT_LAYOUT_GRID",
    "DEFAULT_LAYOUT_LIST",
    "REGIONS",
    "AdaptiveLayoutSelector",
    "LayoutSpec",
    "LiveDashboard",
]
