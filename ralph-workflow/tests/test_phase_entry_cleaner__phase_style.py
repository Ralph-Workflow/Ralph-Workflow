"""Tests for phase_style and show_phase_start display functions."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import (
    phase_style,
    resolve_active_display,
)
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import ArtifactsPolicy, PipelinePolicy


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> tuple[PipelinePolicy, ArtifactsPolicy]:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    return bundle.pipeline, bundle.artifacts


class TestPhaseStyleDisplayStyle:
    """phase_style honors display_style policy field before role-based lookup."""

    def test_phase_style_planning_returns_display_style(self) -> None:
        """planning with display_style='theme.phase.planning' returns that style."""
        pipeline, _ = _load_default_policy_bundle()
        style = phase_style("planning", pipeline)
        assert style == "theme.phase.planning"

    def test_phase_style_development_uses_display_style(self) -> None:
        """development with display_style='theme.phase.development' returns that style."""
        pipeline, _ = _load_default_policy_bundle()
        style = phase_style("development", pipeline)
        assert style == "theme.phase.development"

    def test_phase_style_development_analysis_uses_display_style(self) -> None:
        """development_analysis returns its display_style policy value."""
        pipeline, _ = _load_default_policy_bundle()
        style = phase_style("development_analysis", pipeline)
        assert style == "theme.phase.development_analysis"

    def test_phase_style_development_commit_uses_display_style(self) -> None:
        """development_commit returns its display_style policy value."""
        pipeline, _ = _load_default_policy_bundle()
        style = phase_style("development_commit", pipeline)
        assert style == "theme.phase.development_commit"

    def test_show_phase_start_renders_planning_banner_with_correct_style(
        self,
    ) -> None:
        """emit_phase_start for planning renders output containing 'Planning'."""
        pipeline, _ = _load_default_policy_bundle()
        console = Console(record=True)
        ctx = make_display_context(console=console)
        display = resolve_active_display(None, ctx)
        display.emit_phase_start("planning", pipeline_policy=pipeline)
        output = console.export_text()
        assert "Planning" in output
