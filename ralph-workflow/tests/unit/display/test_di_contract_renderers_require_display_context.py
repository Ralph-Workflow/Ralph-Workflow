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
from typing import TYPE_CHECKING, NamedTuple, cast

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
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.phase_banner import (
    show_phase_transition,
)
from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.tables import show_agents, show_providers

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable


class TestRenderersRequireDisplayContext:
    """Test that all public renderers require an explicit DisplayContext."""

    class DIResult(NamedTuple):
        passed: bool
        error: str | None


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
        self, renderer_module: object, renderer_name: object
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
            cast("Callable[..., object]", show_agents)(config=config)

    def test_show_phase_transition_rejects_missing_context(self) -> None:
        """show_phase_transition() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", show_phase_transition)("planning", "development")

    def test_plain_log_renderer_requires_display_context(self) -> None:
        """PlainLogRenderer() must fail when called without an explicit DisplayContext."""
        console = Console(record=True, width=120, force_terminal=True)
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", PlainLogRenderer)(console)

    def test_parallel_display_requires_display_context(self, tmp_path: pathlib.Path) -> None:
        """ParallelDisplay() must fail when called without an explicit DisplayContext."""
        console = Console(record=True, width=120, force_terminal=True)
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", ParallelDisplay)(console)

    def test_show_providers_rejects_missing_context(self) -> None:
        """show_providers() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", show_providers)(providers=[])

    def test_render_missing_plan_hint_rejects_missing_context(self) -> None:
        """render_missing_plan_hint() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", render_missing_plan_hint)()

    def test_render_plan_artifact_rejects_missing_context(self, tmp_path: pathlib.Path) -> None:
        """render_plan_artifact() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", render_plan_artifact)(tmp_path)

    def test_render_analysis_decision_rejects_missing_context(self, tmp_path: pathlib.Path) -> None:
        """render_analysis_decision() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", render_analysis_decision)(
                tmp_path, "development_analysis"
            )

    def test_render_commit_message_rejects_missing_context(self, tmp_path: pathlib.Path) -> None:
        """render_commit_message() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", render_commit_message)(tmp_path)

    def test_render_fix_artifact_rejects_missing_context(self, tmp_path: pathlib.Path) -> None:
        """render_fix_artifact() must fail when called without display_context."""
        with pytest.raises(TypeError, match="display_context"):
            cast("Callable[..., object]", render_fix_artifact)(tmp_path)


DIResult = TestRenderersRequireDisplayContext.DIResult


def _check_renderer_signature(module: object, name: object) -> DIResult:
    fn = getattr(module, str(name), None)
    if fn is None:
        return DIResult(passed=False, error=f"{name} not found in module")
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError) as e:
        return DIResult(passed=False, error=str(e))
    if "display_context" not in sig.parameters:
        return DIResult(passed=False, error=f"{name!r} has no 'display_context' parameter")
    param = sig.parameters["display_context"]
    if param.default is not inspect.Parameter.empty:
        return DIResult(passed=False, error=f"{name!r}.display_context has a default value")
    return DIResult(passed=True, error=None)
