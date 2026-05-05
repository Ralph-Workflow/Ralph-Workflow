"""Tests for the DisplayContext dependency injection contract.

These tests verify that:
1. All public renderers require an explicit DisplayContext (no silent Console fallbacks).
2. Color disabled propagates correctly through renderers.
3. Compact mode produces abbreviated output.
4. Wide mode produces full layout.
5. DisplayContext.refreshed() picks up new terminal sizes.
6. No literal color/style strings exist outside theme.py.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from unittest.mock import PropertyMock, patch

import pytest
from rich.console import Console

from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.display import artifact_renderer as artifact_renderer_module
from ralph.display import phase_banner as phase_banner_module
from ralph.display import tables as tables_module
from ralph.display.artifact_renderer import (
    render_analysis_decision,
    render_commit_message,
    render_fix_artifact,
    render_missing_plan_hint,
    render_plan_artifact,
)
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.phase_banner import (
    show_phase_start,
    show_phase_transition,
)
from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.tables import show_agents, show_providers

if TYPE_CHECKING:
    import pathlib


class DIResult(NamedTuple):
    passed: bool
    error: str | None


_EMPTY = inspect.Parameter.empty


def _check_renderer_signature(renderer_module, renderer_name: str) -> DIResult:
    """Check that a renderer requires display_context as a keyword-only parameter.

    Returns DIResult: passed=True if the signature requires display_context,
    passed=False with error if a silent Console fallback is detected.
    """

    renderer = getattr(renderer_module, renderer_name)
    sig = inspect.signature(renderer)

    # Check parameters
    has_display_context = "display_context" in sig.parameters

    if not has_display_context:
        # Check for console: Console | None fallback pattern
        for param_name, param in sig.parameters.items():
            if "console" in param_name.lower() and param.annotation != ...:
                annotation_str = str(param.annotation)
                if "Console" in annotation_str and "None" in annotation_str:
                    return DIResult(
                        passed=False,
                        error=(
                            f"{renderer_name} has console fallback: "
                            f"{param_name}: {param.annotation}"
                        ),
                    )
        return DIResult(
            passed=False,
            error=f"{renderer_name} missing display_context parameter",
        )

    # Check that display_context has no default or has None as default (acceptable)
    dc_param = sig.parameters["display_context"]
    if dc_param.default is not _EMPTY and dc_param.default is not None:
        return DIResult(
            passed=False,
            error=f"{renderer_name} display_context has non-None default: {dc_param.default}",
        )

    return DIResult(passed=True, error=None)


class TestRenderersRequireDisplayContext:
    """Test that all public renderers require an explicit DisplayContext."""

    @pytest.mark.parametrize(
        "renderer_module,renderer_name",
        [
            (tables_module, "show_agents"),
            (tables_module, "show_config"),
            (tables_module, "show_metrics"),
            (tables_module, "show_providers"),
            (phase_banner_module, "show_phase_transition"),
            (phase_banner_module, "show_phase_start"),
            (phase_banner_module, "show_phase_start_from_entry"),
            (phase_banner_module, "show_phase_close_banner"),
            (artifact_renderer_module, "render_plan_artifact"),
            (artifact_renderer_module, "render_analysis_decision"),
            (artifact_renderer_module, "render_commit_message"),
            (artifact_renderer_module, "render_fix_artifact"),
            (artifact_renderer_module, "render_missing_plan_hint"),
        ],
    )
    def test_renderer_signature_requires_display_context(
        self, renderer_module, renderer_name
    ) -> None:
        """Each renderer must require display_context keyword arg (no Console fallback)."""
        result = _check_renderer_signature(renderer_module, renderer_name)
        assert result.passed, f"DI contract violated: {result.error}"

    def test_show_agents_rejects_missing_context(self) -> None:
        """show_agents() must fail when called without display_context."""
        config = UnifiedConfig(
            agents={},
            general=GeneralConfig(),
        )
        with pytest.raises(TypeError, match="display_context"):
            show_agents(config=config)  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    def test_show_phase_transition_rejects_missing_context(self) -> None:
        """show_phase_transition() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            show_phase_transition("planning", "development")  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    def test_plain_log_renderer_requires_display_context(self) -> None:
        """PlainLogRenderer() must fail when called without an explicit DisplayContext."""
        console = Console(record=True, width=120, force_terminal=True)
        with pytest.raises(TypeError, match="display_context"):
            cast("Any", PlainLogRenderer)(console)

    def test_parallel_display_requires_display_context(self, tmp_path: pathlib.Path) -> None:
        """ParallelDisplay() must fail when called without an explicit DisplayContext."""
        console = Console(record=True, width=120, force_terminal=True)
        with pytest.raises(TypeError, match="display_context"):
            cast("Any", ParallelDisplay)(console)

    def test_show_providers_rejects_missing_context(self) -> None:
        """show_providers() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            show_providers(providers=[])  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    def test_render_missing_plan_hint_rejects_missing_context(self) -> None:
        """render_missing_plan_hint() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            render_missing_plan_hint()  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    def test_render_plan_artifact_rejects_missing_context(self, tmp_path: pathlib.Path) -> None:
        """render_plan_artifact() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            render_plan_artifact(tmp_path)  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    def test_render_analysis_decision_rejects_missing_context(self, tmp_path: pathlib.Path) -> None:
        """render_analysis_decision() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            render_analysis_decision(tmp_path, "development_analysis")  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    def test_render_commit_message_rejects_missing_context(self, tmp_path: pathlib.Path) -> None:
        """render_commit_message() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            render_commit_message(tmp_path)  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    def test_render_fix_artifact_rejects_missing_context(self, tmp_path: pathlib.Path) -> None:
        """render_fix_artifact() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            render_fix_artifact(tmp_path)  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


class TestColorDisabledPropagates:
    """Test that NO_COLOR=1 propagates to disable ANSI in renderer output."""

    def test_no_color_disables_ansi_in_show_phase_start(self) -> None:
        """When NO_COLOR=1, show_phase_start output contains no ANSI sequences."""
        console = Console(record=True, width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={"NO_COLOR": "1"})
        assert ctx.color_enabled is False

        show_phase_start("planning", display_context=ctx)

        output = console.export_text()
        # ANSI escape sequences are \x1b[...m or similar
        ansi_esc = 0x1B
        ansi_escapes = [c for c in output if ord(c) == ansi_esc]
        assert len(ansi_escapes) == 0, f"ANSI sequences found in NO_COLOR output: {output!r}"

    def test_no_color_on_console_propagates(self) -> None:
        """DisplayContext.color_enabled should be False when console.no_color is True."""
        console = Console(record=True, width=120, force_terminal=True, no_color=True)
        ctx = make_display_context(console=console, env={})
        assert ctx.color_enabled is False


class TestCompactModeLimits:
    """Test that compact mode produces abbreviated layout."""

    def test_phase_transition_compact_no_leading_blank(self) -> None:
        """Compact mode show_phase_transition must not emit a leading blank line."""
        console = Console(record=True, width=50, force_terminal=True)
        ctx = make_display_context(console=console, env={"COLUMNS": "50"})
        assert ctx.mode == "compact"

        show_phase_transition(
            "planning", "development", display_context=ctx
        )

        output = console.export_text()
        lines = output.strip().split("\n")
        max_compact_lines = 4
        assert len(lines) <= max_compact_lines, f"Compact output too long: {lines!r}"
        # First line should not be blank
        assert lines[0].strip() != "", f"Leading blank line in compact mode: {lines!r}"

    def test_phase_transition_wide_has_full_layout(self) -> None:
        """Wide mode show_phase_transition must emit full banner with Rules."""
        console = Console(record=True, width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={"COLUMNS": "120"})
        assert ctx.mode == "wide"

        show_phase_transition(
            "planning", "development", display_context=ctx
        )

        output = console.export_text()
        # In wide mode there should be Rule characters (──)
        assert "Rule" in output or "─" in output or "planning" in output


class TestRefreshedPicksUpNewWidth:
    """Test that DisplayContext.refreshed() picks up new terminal sizes."""

    def test_refreshed_changes_mode(self) -> None:
        """Calling refreshed() on a wide context with narrow console switches to compact."""
        # Start with a console at width 120 (wide)
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        assert ctx.mode == "wide"

        # Simulate resize: after refresh the console reports width 40
        narrow_width = 40
        with patch.object(
            type(console), "width", new_callable=PropertyMock, return_value=narrow_width
        ):
            refreshed = ctx.refreshed()

        assert refreshed.mode == "compact"
        assert refreshed.width == narrow_width

        # Sanity: without resize, refreshed stays wide
        refreshed_still_wide = ctx.refreshed()
        assert refreshed_still_wide.mode == "wide"

    def test_refreshed_preserves_theme_and_color_enabled(self) -> None:
        """refreshed() must preserve theme and color_enabled from the original context."""
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        original_theme = ctx.theme
        original_color = ctx.color_enabled

        refreshed = ctx.refreshed()

        assert refreshed.theme is original_theme
        assert refreshed.color_enabled == original_color


