"""Tests for ParallelDisplay's log-first output mode."""

from __future__ import annotations

from rich.console import Console

from ralph.display.parallel_display import (
    NARROW_THRESHOLD,
    ParallelDisplay,
    _strip_markup,
    detect_mode,
)
from ralph.pipeline.worker_state import WorkerStatus


def test_ci_env_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"CI": "1"}) == "lines"


def test_ci_empty_string_still_prefers_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"CI": ""}) == "lines"


def test_no_color_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"NO_COLOR": "1"}) == "lines"


def test_no_color_empty_string_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"NO_COLOR": ""}) == "lines"


def test_term_dumb_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"TERM": "dumb"}) == "lines"


def test_term_other_value_still_prefers_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"TERM": "xterm-256color"}) == "lines"


def test_non_terminal_console_forces_lines() -> None:
    console = Console(force_terminal=False, width=120)
    assert detect_mode(console, {}) == "lines"


def test_narrow_terminal_forces_lines() -> None:
    console = Console(force_terminal=True, width=40)
    assert detect_mode(console, {}) == "lines"


def test_threshold_boundary_forces_lines() -> None:
    console = Console(force_terminal=True, width=NARROW_THRESHOLD)
    assert detect_mode(console, {}) == "lines"


def test_threshold_plus_one_still_prefers_lines() -> None:
    console = Console(force_terminal=True, width=NARROW_THRESHOLD + 1)
    assert detect_mode(console, {}) == "lines"


def test_parallel_display_mode_detected_at_init() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    assert pd.mode == "lines"


def test_parallel_display_mode_lines_when_ci() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {"CI": "1"})
    assert pd.mode == "lines"


def test_parallel_display_default_env_uses_os_environ() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console)
    assert pd.mode == "lines"


def test_parallel_display_mode_frozen_after_init() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    try:
        pd.mode = "lines"  # type: ignore[misc]
        raise AssertionError("Should have raised AttributeError")
    except AttributeError:
        pass


def test_parallel_display_context_manager() -> None:
    console = Console(force_terminal=True, width=120)
    with ParallelDisplay(console, {}) as pd:
        assert pd.mode == "lines"


def test_parallel_display_emit_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.emit("unit-1", "some output line")


def test_parallel_display_emit_none_unit_id_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.emit(None, "some output line")


def test_parallel_display_set_status_writes_line() -> None:
    console = Console(force_terminal=True, width=120, record=True)
    pd = ParallelDisplay(console, {})
    pd.set_status("unit-1", WorkerStatus.RUNNING)
    assert "status=RUNNING" in console.export_text()


def test_parallel_display_start_stop_do_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.start()
    pd.stop()


def test_parallel_display_default_mode_streams_copy_pasteable_lines() -> None:
    console = Console(force_terminal=True, width=120, record=True)
    pd = ParallelDisplay(console, {})

    assert pd.mode == "lines"

    pd.start()
    try:
        pd.emit("unit-1", "[green]some output line[/green]")
    finally:
        pd.stop()

    rendered_text = console.export_text()
    assert "some output line" in rendered_text
    assert "[green]" not in rendered_text
    assert "[/green]" not in rendered_text
    assert "Agent Activity" not in rendered_text


def test_strip_markup_removes_rich_tags() -> None:
    assert _strip_markup("[green]ok[/green]") == "ok"
    assert _strip_markup("plain text") == "plain text"
