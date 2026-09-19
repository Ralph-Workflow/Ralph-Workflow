"""Regression tests for validation-failure phase-close severity."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.phase_lifecycle import PhaseExitModel
from ralph.recovery.retry_prompt import VALIDATION_FAILURE_BANNER


def _render(last_failure_category: str | None) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=200)
    display = ParallelDisplay(make_display_context(console=console, env={}))
    display.emit_phase_close_from_exit(
        PhaseExitModel(
            phase_name="development",
            artifact_outcome="success",
            last_failure_category=last_failure_category,
        )
    )
    return output.getvalue()


def test_artifact_validation_phase_close_is_error_and_not_success() -> None:
    output = _render("artifact_validation")

    assert VALIDATION_FAILURE_BANNER in output
    assert "WARN" not in output
    assert "success" not in output.lower()


def test_other_phase_close_categories_retain_warn_debug_behavior() -> None:
    output = _render("environmental")

    assert "[phase-close] debug" in output
    assert "VALIDATION FAILURE" not in output
    assert "ERROR" not in output
