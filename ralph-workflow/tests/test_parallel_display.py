"""Tests for ParallelDisplay mode detection."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from rich.console import Console, RenderableType
from rich.live import Live

from ralph.display.parallel_display import (
    NARROW_THRESHOLD,
    ParallelDisplay,
    _dashboard_renderable,
    detect_mode,
)
from ralph.pipeline.worker_state import WorkerStatus

EXPECTED_QUEUE_UPDATES = 2


def test_ci_env_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"CI": "1"}) == "lines"


def test_ci_empty_string_does_not_force_lines() -> None:
    """CI='' is falsy — should not trigger lines mode on its own."""
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"CI": ""}) == "dashboard"


def test_no_color_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"NO_COLOR": "1"}) == "lines"


def test_no_color_empty_string_forces_lines() -> None:
    """NO_COLOR='' — merely being set (even empty) triggers lines mode."""
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"NO_COLOR": ""}) == "lines"


def test_term_dumb_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"TERM": "dumb"}) == "lines"


def test_term_other_value_does_not_force_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"TERM": "xterm-256color"}) == "dashboard"


def test_non_terminal_console_forces_lines() -> None:
    console = Console(force_terminal=False, width=120)
    assert detect_mode(console, {}) == "lines"


def test_narrow_terminal_forces_lines() -> None:
    console = Console(force_terminal=True, width=40)
    assert detect_mode(console, {}) == "lines"


def test_threshold_boundary_forces_lines() -> None:
    """Width exactly at NARROW_THRESHOLD is still too narrow."""
    console = Console(force_terminal=True, width=NARROW_THRESHOLD)
    assert detect_mode(console, {}) == "lines"


def test_threshold_plus_one_is_dashboard() -> None:
    console = Console(force_terminal=True, width=NARROW_THRESHOLD + 1)
    assert detect_mode(console, {}) == "dashboard"


def test_dashboard_mode_when_all_good() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {}) == "dashboard"


def test_parallel_display_mode_detected_at_init() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    assert pd.mode == "dashboard"


def test_parallel_display_mode_lines_when_ci() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {"CI": "1"})
    assert pd.mode == "lines"


def test_parallel_display_default_env_uses_os_environ() -> None:
    """Passing env=None should not crash (uses os.environ internally)."""
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console)
    assert pd.mode in ("dashboard", "lines")


def test_parallel_display_mode_frozen_after_init() -> None:
    """mode attribute must not be writable."""
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    try:
        pd.mode = "lines"  # type: ignore[misc]  # reason: assert runtime immutability guard
        raise AssertionError("Should have raised AttributeError")
    except AttributeError:
        pass


def test_parallel_display_context_manager() -> None:
    """__enter__/__exit__ must not raise."""
    console = Console(force_terminal=True, width=120)
    with ParallelDisplay(console, {}) as pd:
        assert pd.mode in ("dashboard", "lines")


def test_parallel_display_emit_stub_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.emit("unit-1", "some output line")


def test_parallel_display_emit_none_unit_id_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.emit(None, "some output line")


def test_parallel_display_set_status_stub_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.set_status("unit-1", WorkerStatus.RUNNING)


def test_parallel_display_dashboard_mode_uses_put_nowait_for_updates() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {}, mode="dashboard")
    queued: list[object] = []

    class _QueueSpy:
        def put(self, _item: object) -> None:
            raise AssertionError("blocking put() should not be used")

        def put_nowait(self, item: object) -> None:
            queued.append(item)

    pd._queue = _QueueSpy()  # type: ignore[assignment]  # reason: inject queue spy for put_nowait assertion

    pd.emit("unit-1", "some output line")
    pd.set_status("unit-1", WorkerStatus.RUNNING)

    assert len(queued) == EXPECTED_QUEUE_UPDATES


def test_parallel_display_start_stop_stubs_do_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.start()
    pd.stop()


def test_parallel_display_dashboard_mode_renders_emitted_output(monkeypatch) -> None:
    console = Console(force_terminal=True, width=120, record=True)
    pd = ParallelDisplay(console, {})
    renderables: list[RenderableType] = []
    original_update = Live.update

    def capture_update(
        self: Live,
        renderable: RenderableType,
        *args: object,
        **kwargs: object,
    ) -> None:
        renderables.append(renderable)
        original_update(self, renderable, *args, **kwargs)

    monkeypatch.setattr(Live, "update", capture_update)

    assert pd.mode == "dashboard"

    pd.start()
    try:
        pd.emit("unit-1", "some output line")
        time.sleep(0.35)
    finally:
        pd.stop()

    dashboard_console = Console(record=True, width=120, force_terminal=True)
    for renderable in renderables:
        dashboard_console.print(renderable)
    assert "some output line" in dashboard_console.export_text()


def test_dashboard_renderable_uses_worker_elapsed_time() -> None:
    started_at = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
    finished_at = started_at + timedelta(seconds=3.5)
    renderable = _dashboard_renderable(
        {"unit-1": ["line-1"]},
        worker_status={
            "unit-1": {
                "status": WorkerStatus.SUCCEEDED,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        },
    )

    console = Console(record=True, width=120, force_terminal=True)
    console.print(renderable)

    assert "3.5" in console.export_text()


def test_dashboard_renderable_reports_no_dropped_lines_for_single_source() -> None:
    renderable = _dashboard_renderable({"unit-1": ["line-1", "line-2"]})

    console = Console(record=True, width=120, force_terminal=True)
    console.print(renderable)

    assert "dropped:" not in console.export_text()
