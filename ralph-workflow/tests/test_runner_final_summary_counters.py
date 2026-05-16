"""Tests that _emit_final_summary extracts activity counters from display._plain_renderer."""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from unittest.mock import patch

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULT_POLICY = load_policy(Path(__file__).parent.parent / "ralph" / "policy" / "defaults")

_CONTENT_BLOCKS = 7
_THINKING_BLOCKS = 3
_TOOL_CALLS = 12
_ERRORS = 1


def _make_parallel_display(tmp_path: Path) -> tuple[ParallelDisplay, Console]:
    console = Console(
        record=True,
        width=120,
        force_terminal=False,
        color_system=None,
        highlight=False,
    )
    subscriber = PipelineSubscriber(
        queue=Queue(maxsize=64),
        workspace_root=tmp_path,
        run_id="test-run",
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )
    display = ParallelDisplay(
        make_display_context(console=console, env={}),
        workspace_root=tmp_path,
        subscriber=subscriber,
    )
    return display, console


def test_final_summary_passes_plain_renderer_counters_to_emit(tmp_path: Path) -> None:
    """_emit_final_summary forwards activity counters from display._plain_renderer."""
    display, _console = _make_parallel_display(tmp_path)

    plain_renderer = display._plain_renderer
    plain_renderer._run_counters.content_blocks = _CONTENT_BLOCKS
    plain_renderer._run_counters.thinking_blocks = _THINKING_BLOCKS
    plain_renderer._run_counters.tool_calls = _TOOL_CALLS
    plain_renderer._run_counters.errors = _ERRORS

    state = PipelineState(phase="complete")
    ctx = make_display_context(console=_console, env={})

    captured_kwargs: dict = {}

    def _capture(*_args: object, **kwargs: object) -> None:
        captured_kwargs.update(kwargs)

    with patch(
        "ralph.display.completion_summary.emit_completion_summary",
        side_effect=_capture,
    ):
        runner_module._emit_final_summary(
            state,
            tmp_path,
            display=display,
            display_context=ctx,
        )

    opts = captured_kwargs.get("options")
    assert opts is not None
    assert opts.content_block_count == _CONTENT_BLOCKS
    assert opts.thinking_block_count == _THINKING_BLOCKS
    assert opts.tool_call_count == _TOOL_CALLS
    assert opts.error_count == _ERRORS


def test_final_summary_defaults_counters_to_zero_when_no_display(tmp_path: Path) -> None:
    """_emit_final_summary uses zero defaults when display has no _plain_renderer."""
    state = PipelineState(phase="complete")
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console, env={})

    captured_kwargs: dict = {}

    def _capture(*_args: object, **kwargs: object) -> None:
        captured_kwargs.update(kwargs)

    with patch(
        "ralph.display.completion_summary.emit_completion_summary",
        side_effect=_capture,
    ):
        runner_module._emit_final_summary(
            state,
            tmp_path,
            display=None,
            display_context=ctx,
        )

    opts = captured_kwargs.get("options")
    assert opts is not None
    assert opts.content_block_count == 0
    assert opts.thinking_block_count == 0
    assert opts.tool_call_count == 0
    assert opts.error_count == 0
