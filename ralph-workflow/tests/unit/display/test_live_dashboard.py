from __future__ import annotations

import queue
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel

from ralph.display.live_dashboard import (
    DEFAULT_LAYOUT_GRID,
    DEFAULT_LAYOUT_LIST,
    AdaptiveLayoutSelector,
    LayoutSpec,
    LiveDashboard,
)
from ralph.display.snapshot import DashboardSnapshot, WorkerSnapshot


def _make_worker(unit_id: str = "w1") -> WorkerSnapshot:
    return WorkerSnapshot(
        unit_id=unit_id,
        description="test worker",
        status="RUNNING",
        status_semantic="running",
        started_at=datetime.now(UTC),
        finished_at=None,
        elapsed_s=0.0,
        exit_code=None,
        commit_sha=None,
        error_message=None,
    )


def _make_snapshot(n_workers: int = 1) -> DashboardSnapshot:
    workers = tuple(_make_worker(f"w{i}") for i in range(n_workers))
    return DashboardSnapshot(
        phase="build",
        previous_phase=None,
        iteration=1,
        total_iterations=3,
        reviewer_pass=0,
        total_reviewer_passes=1,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=workers,
        prompt_path=None,
        prompt_preview=(),
        run_id="test-run",
        created_at=datetime.now(UTC),
    )


def _make_mock_panels() -> tuple[MagicMock, ...]:
    needed: set[str] = set()
    for spec in (DEFAULT_LAYOUT_GRID, DEFAULT_LAYOUT_LIST):
        for name, _, _ in spec.regions:
            needed.add(name)
    panels = []
    for name in needed:
        p = MagicMock()
        p.name = name
        p.render.return_value = f"[{name}]"
        panels.append(p)
    return tuple(panels)


def _make_dashboard(
    *,
    panels: tuple | None = None,
    layout_selector=None,
    clock=None,
) -> tuple[LiveDashboard, queue.Queue]:
    console = Console(record=True, width=120, force_terminal=False)
    snap_queue: queue.Queue[DashboardSnapshot] = queue.Queue()
    if panels is None:
        panels = _make_mock_panels()
    kwargs: dict = {
        "console": console,
        "panels": panels,
        "buffers": {},
        "snapshot_queue": snap_queue,
    }
    if layout_selector is not None:
        kwargs["layout_selector"] = layout_selector
    if clock is not None:
        kwargs["clock"] = clock
    return LiveDashboard(**kwargs), snap_queue


class TestAdaptiveLayoutSelector:
    def test_grid_for_3_workers(self) -> None:
        sel = AdaptiveLayoutSelector()
        assert sel(_make_snapshot(3), terminal_width=120) is DEFAULT_LAYOUT_GRID

    def test_list_for_5_workers(self) -> None:
        sel = AdaptiveLayoutSelector()
        assert sel(_make_snapshot(5), terminal_width=120) is DEFAULT_LAYOUT_LIST

    def test_grid_at_boundary_4_workers(self) -> None:
        sel = AdaptiveLayoutSelector()
        assert sel(_make_snapshot(4), terminal_width=120) is DEFAULT_LAYOUT_GRID


class TestLayoutSpec:
    def test_frozen(self) -> None:
        spec = LayoutSpec(name="x", regions=(("header", 3, None),))
        with pytest.raises((AttributeError, TypeError)):
            spec.name = "y"  # type: ignore[misc]


class TestLiveDashboard:
    def test_update_does_not_raise_on_zero_worker_snapshot(self) -> None:
        dash, _ = _make_dashboard()
        dash.update(_make_snapshot(0))

    def test_panel_name_typo_raises_keyerror_at_construction(self) -> None:
        all_panels = _make_mock_panels()
        panels_without_header = tuple(p for p in all_panels if p.name != "header")
        console = Console(record=True, width=120, force_terminal=False)
        snap_queue: queue.Queue[DashboardSnapshot] = queue.Queue()
        with pytest.raises(KeyError, match="header"):
            LiveDashboard(
                console=console,
                panels=panels_without_header,
                buffers={},
                snapshot_queue=snap_queue,
            )

    def test_update_is_nonblocking(self) -> None:
        dash, _ = _make_dashboard()
        snap = _make_snapshot(1)
        start = time.perf_counter()
        dash.update(snap)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 1.0, f"update() took {elapsed_ms:.2f} ms"

    def test_context_manager_non_tty_does_not_raise(self) -> None:
        dash, _ = _make_dashboard()
        with dash:
            pass

    def test_clock_injection(self) -> None:
        fake_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        dash, _ = _make_dashboard(clock=lambda: fake_time)
        assert dash._clock() == fake_time

    def test_layout_selector_injection(self) -> None:
        always_list = MagicMock(return_value=DEFAULT_LAYOUT_LIST)
        dash, _ = _make_dashboard(layout_selector=always_list)
        snap = _make_snapshot(2)
        dash._latest_snapshot = snap
        dash._render_once()
        always_list.assert_called_once_with(snap, terminal_width=dash._console.width)

    def test_render_once_returns_placeholder_with_no_snapshot(self) -> None:
        dash, _ = _make_dashboard()
        result = dash._render_once()
        assert isinstance(result, Panel)

    def test_render_once_returns_layout_with_snapshot(self) -> None:
        dash, _ = _make_dashboard()
        dash._latest_snapshot = _make_snapshot(3)
        result = dash._render_once()
        assert isinstance(result, Layout)
