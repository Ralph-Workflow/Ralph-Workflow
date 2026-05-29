"""Integration test for SIGWINCH-driven display resize during a pipeline run.

This test verifies that:
1. When SIGWINCH fires during a pipeline run, the DisplayContext is refreshed.
2. After resize, phase banners use the new terminal width and mode.
3. Pre-resize and post-resize transcripts differ in expected ways.
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import PropertyMock, patch

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.phase_banner import show_phase_transition
from ralph.display.plain_renderer import PlainLogRenderer


class TestSigwinchIntegration:
    """Integration tests for SIGWINCH resize behavior."""

    def test_plain_renderer_uses_refreshed_context_at_phase_boundary(self) -> None:
        """After SIGWINCH, PlainLogRenderer.flush_blocks() picks up the new context."""
        if sys.platform == "win32":
            pytest.skip("SIGWINCH not available on Windows")

        # Create a wide console
        wide_console = Console(file=StringIO(), width=120, force_terminal=True)
        wide_ctx = make_display_context(
            console=wide_console,
            env={"COLUMNS": "120"},
            force_width=120,
        )
        assert wide_ctx.mode == "wide"

        renderer = PlainLogRenderer(wide_ctx)

        # Verify initial mode is wide
        assert renderer._ctx.mode == "wide"

        # Simulate SIGWINCH: while the console is narrow (patched),
        # SIGWINCH handler calls refreshed() and updates renderer._ctx.
        # Then flush_blocks() is called while still in narrow state.
        with patch.object(type(wide_console), "width", new_callable=PropertyMock, return_value=40):
            # SIGWINCH fires and calls refreshed()
            refreshed_ctx = renderer._ctx.refreshed()
            assert refreshed_ctx.mode == "compact"

            # SIGWINCH handler updates the renderer's context
            renderer._ctx = refreshed_ctx

            # flush_blocks() also calls refreshed() but should stay compact
            renderer.flush_blocks()

            assert renderer._ctx.mode == "compact"

    def test_phase_banner_adapts_to_context_mode(self) -> None:
        """show_phase_transition output differs between compact and wide mode."""
        # Compact mode output
        compact_console = Console(record=True, width=50, force_terminal=True)
        compact_ctx = make_display_context(console=compact_console, env={"COLUMNS": "50"})
        show_phase_transition("planning", "development", display_context=compact_ctx)
        compact_output = compact_console.export_text()

        # Wide mode output
        wide_console = Console(record=True, width=120, force_terminal=True)
        wide_ctx = make_display_context(console=wide_console, env={"COLUMNS": "120"})
        show_phase_transition("planning", "development", display_context=wide_ctx)
        wide_output = wide_console.export_text()

        # Wide output should have more content (additional description, rules)
        # Compact output should be more terse
        assert len(wide_output) > len(compact_output), (
            f"Expected wide output ({len(wide_output)}) to be longer than "
            f"compact output ({len(compact_output)})"
        )

        # Both should contain the phase names (case-insensitive check)
        assert "planning" in compact_output.lower()
        assert "development" in compact_output.lower()
        assert "planning" in wide_output.lower()
        assert "development" in wide_output.lower()

    def test_display_context_snapshot_differs_after_resize(self) -> None:
        """Two DisplayContexts created with different widths have different adaptive limits."""
        narrow_ctx = make_display_context(env={"COLUMNS": "40"})
        medium_ctx = make_display_context(env={"COLUMNS": "80"})
        wide_ctx = make_display_context(env={"COLUMNS": "120"})

        assert narrow_ctx.mode == "compact"
        assert medium_ctx.mode == "medium"
        assert wide_ctx.mode == "wide"

        # Adaptive limits increase with width
        assert narrow_ctx.headline_max_chars < medium_ctx.headline_max_chars
        assert medium_ctx.headline_max_chars < wide_ctx.headline_max_chars
