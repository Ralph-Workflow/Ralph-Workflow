"""Tests for dashboard renderer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.renderers.dashboard import render_dashboard

if TYPE_CHECKING:
    from ralph.display.renderers.dashboard import DashboardState

from ralph.pipeline.worker_state import WorkerStatus


def test_all_status_labels() -> None:
    """Test all four status labels appear in dashboard output."""
    state: dict[str, DashboardState] = {
        "u1": {
            "unit_id": "u1",
            "status": WorkerStatus.RUNNING,
            "elapsed_s": 1.0,
            "last_output": "running output",
            "dropped": 0,
        },
        "u2": {
            "unit_id": "u2",
            "status": WorkerStatus.SUCCEEDED,
            "elapsed_s": 5.0,
            "last_output": "done output",
            "dropped": 0,
        },
        "u3": {
            "unit_id": "u3",
            "status": WorkerStatus.FAILED,
            "elapsed_s": 2.0,
            "last_output": "error output",
            "dropped": 0,
        },
        "u4": {
            "unit_id": "u4",
            "status": WorkerStatus.PENDING,
            "elapsed_s": 0.0,
            "last_output": "",
            "dropped": 0,
        },
    }

    console = Console(record=True, width=120, force_terminal=True)
    renderable = render_dashboard(state)
    console.print(renderable)
    text = console.export_text()

    assert "RUN" in text
    assert "DONE" in text
    assert "FAIL" in text
    assert "WAIT" in text


def test_status_cancelled_shows_wait() -> None:
    """Test CANCELLED status displays as WAIT label."""
    state: dict[str, DashboardState] = {
        "u1": {
            "unit_id": "u1",
            "status": WorkerStatus.CANCELLED,
            "elapsed_s": 0.0,
            "last_output": "",
            "dropped": 0,
        },
    }

    console = Console(record=True, width=120, force_terminal=True)
    renderable = render_dashboard(state)
    console.print(renderable)
    text = console.export_text()

    assert "WAIT" in text
    assert "RUN" not in text


def test_dropped_footer_appears_when_non_zero() -> None:
    """Test dropped counter appears in footer when any unit has dropped > 0."""
    state: dict[str, DashboardState] = {
        "u1": {
            "unit_id": "u1",
            "status": WorkerStatus.RUNNING,
            "elapsed_s": 1.0,
            "last_output": "output",
            "dropped": 5,
        },
    }

    console = Console(record=True, width=120, force_terminal=True)
    renderable = render_dashboard(state)
    console.print(renderable)
    text = console.export_text()

    assert "dropped: 5 lines" in text


def test_dropped_footer_hidden_when_zero() -> None:
    """Test dropped counter does not appear when all units have dropped == 0."""
    state: dict[str, DashboardState] = {
        "u1": {
            "unit_id": "u1",
            "status": WorkerStatus.SUCCEEDED,
            "elapsed_s": 1.0,
            "last_output": "success",
            "dropped": 0,
        },
    }

    console = Console(record=True, width=120, force_terminal=True)
    renderable = render_dashboard(state)
    console.print(renderable)
    text = console.export_text()

    assert "dropped" not in text.lower()


def test_last_output_truncated_to_80_chars() -> None:
    """Test last output is truncated to 80 characters."""
    long_output = "x" * 200
    state: dict[str, DashboardState] = {
        "u1": {
            "unit_id": "u1",
            "status": WorkerStatus.RUNNING,
            "elapsed_s": 1.0,
            "last_output": long_output,
            "dropped": 0,
        },
    }

    console = Console(record=True, width=120, force_terminal=True)
    renderable = render_dashboard(state)
    console.print(renderable)
    text = console.export_text()

    assert "x" * 80 in text
    assert ("x" * 81) not in text


def test_elapsed_time_displayed() -> None:
    """Test elapsed time is displayed in output."""
    state: dict[str, DashboardState] = {
        "u1": {
            "unit_id": "u1",
            "status": WorkerStatus.RUNNING,
            "elapsed_s": 12.5,
            "last_output": "output",
            "dropped": 0,
        },
    }

    console = Console(record=True, width=120, force_terminal=True)
    renderable = render_dashboard(state)
    console.print(renderable)
    text = console.export_text()

    assert "12.5" in text


def test_unit_id_column_displayed() -> None:
    """Test unit ID is shown in first column."""
    state: dict[str, DashboardState] = {
        "my-unit-42": {
            "unit_id": "my-unit-42",
            "status": WorkerStatus.PENDING,
            "elapsed_s": 0.0,
            "last_output": "",
            "dropped": 0,
        },
    }

    console = Console(record=True, width=120, force_terminal=True)
    renderable = render_dashboard(state)
    console.print(renderable)
    text = console.export_text()

    assert "my-unit-42" in text
