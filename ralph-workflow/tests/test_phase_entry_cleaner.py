"""Tests for ralph/pipeline/phase_entry_cleaner.py."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.phase_banner import phase_style, show_phase_start
from ralph.pipeline.phase_entry_cleaner import (
    clear_phase_entry_drains,
    is_fresh_phase_entry,
)
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseDefinition,
    PhaseRole,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.policy.models import ArtifactsPolicy


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> tuple[PipelinePolicy, ArtifactsPolicy]:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    return bundle.pipeline, bundle.artifacts


def _make_phase_def(
    role: PhaseRole,
    on_loopback: str | None = None,
    **extra: object,
) -> PhaseDefinition:
    """Build a minimal PhaseDefinition for testing."""
    from ralph.policy.models import PhaseDefinition, PhaseTransition

    kwargs: dict[str, object] = {
        "drain": "test_phase",
        "transitions": PhaseTransition(on_success="next"),
    }
    if role:
        kwargs["role"] = role
    if on_loopback:
        kwargs["transitions"] = PhaseTransition(on_success="next", on_loopback=on_loopback)
    kwargs.update(extra)
    return PhaseDefinition(**kwargs)


def _make_minimal_pipeline(
    phases: dict[str, PhaseDefinition],
) -> PipelinePolicy:
    """Build a minimal PipelinePolicy for testing."""
    from ralph.policy.models import PhaseTransition, PipelinePolicy, RecoveryPolicy

    all_phases = dict(phases)
    all_phases["terminal"] = PhaseDefinition(
        drain="terminal",
        role="terminal",
        terminal_outcome="success",
        transitions=PhaseTransition(on_success="terminal"),
    )
    return PipelinePolicy(
        phases=all_phases,
        entry_phase=next(iter(phases)),
        terminal_phase="terminal",
        recovery=RecoveryPolicy(failed_route="terminal"),
    )


# ---------------------------------------------------------------------------
# is_fresh_phase_entry tests
# ---------------------------------------------------------------------------


class TestIsFreshPhaseEntry:
    """is_fresh_phase_entry returns True for genuine fresh entry, False otherwise."""

    def test_previous_phase_none_is_fresh(self) -> None:
        """previous_phase=None (program start or last-commit→planning) is fresh."""
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("planning", None, pipeline) is True

    def test_same_phase_is_not_fresh(self) -> None:
        """previous_phase == entering_phase (same-phase retry) is NOT fresh."""
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("planning", "planning", pipeline) is False
        assert is_fresh_phase_entry("development", "development", pipeline) is False

    def test_analysis_loopback_is_not_fresh(self) -> None:
        """previous_phase is analysis with on_loopback==entering_phase is NOT fresh."""
        pipeline, _ = _load_default_policy_bundle()
        # planning_analysis has on_loopback="planning"
        assert is_fresh_phase_entry("planning", "planning_analysis", pipeline) is False
        # development_analysis has on_loopback="development"
        assert is_fresh_phase_entry("development", "development_analysis", pipeline) is False

    def test_unrelated_cross_phase_is_fresh(self) -> None:
        """previous_phase is a different, unrelated phase is fresh."""
        pipeline, _ = _load_default_policy_bundle()
        # dev enters with previous=planning_analysis (cross-phase transition)
        assert is_fresh_phase_entry("development", "planning_analysis", pipeline) is True
        # last-commit re-entry to planning
        assert is_fresh_phase_entry("planning", "development_commit", pipeline) is True

    def test_unknown_previous_phase_is_fresh(self) -> None:
        """previous_phase not in pipeline phases is treated as fresh."""
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("planning", "nonexistent_phase", pipeline) is True

    def test_development_commit_from_development_is_fresh(self) -> None:
        """development_commit from development (cross-phase transition) is fresh."""
        pipeline, _ = _load_default_policy_bundle()
        # development_analysis has on_loopback="development", so dev->dev_analysis is not fresh
        # but development_commit is a different phase, so it IS fresh
        assert is_fresh_phase_entry("development_commit", "development", pipeline) is True


# ---------------------------------------------------------------------------
# clear_phase_entry_drains tests
# ---------------------------------------------------------------------------


def _write_artifact_files(
    ws: FsWorkspace,
    artifact_type: str,
    json_path: str,
    md_path: str | None,
) -> None:
    """Write minimal artifact files for a given artifact type."""
    ws.mkdirs(Path(json_path).parent.as_posix())
    ws.write(json_path, json.dumps({"type": artifact_type, "content": "test"}))
    if md_path:
        ws.write(md_path, f"# {artifact_type}\n\ntest content")


class TestClearPhaseEntryDrains:
    """clear_phase_entry_drains deletes drain artifacts on fresh entry only."""

    def test_fresh_entry_clears_declared_drains(
        self, tmp_path: Path
    ) -> None:
        """Fresh entry with declared drains: JSON and markdown files are deleted."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        # Pre-create planning and planning_analysis drain artifacts
        _write_artifact_files(
            ws, "plan",
            ".agent/artifacts/plan.json",
            ".agent/PLAN.md",
        )
        _write_artifact_files(
            ws, "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.json",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        # Also create a development artifact (should NOT be cleared on planning entry)
        _write_artifact_files(
            ws, "development_result",
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )

        clear_phase_entry_drains(ws, "planning", None, pipeline, artifacts_policy)

        # planning and planning_analysis artifacts are cleared
        assert not ws.exists(".agent/artifacts/plan.json")
        assert not ws.exists(".agent/PLAN.md")
        assert not ws.exists(".agent/artifacts/planning_analysis_decision.json")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        # development artifact is NOT cleared (not in planning's clear_drains_on_fresh_entry)
        assert ws.exists(".agent/artifacts/development_result.json")
        assert ws.exists(".agent/DEVELOPMENT_RESULT.md")

    def test_loopback_does_not_clear(self, tmp_path: Path) -> None:
        """Analysis loopback: is_fresh=False → files are NOT deleted."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws, "plan",
            ".agent/artifacts/plan.json",
            ".agent/PLAN.md",
        )

        # planning_analysis → planning loopback (previous_phase="planning_analysis")
        clear_phase_entry_drains(ws, "planning", "planning_analysis", pipeline, artifacts_policy)

        # Files must still exist (loopback, not fresh)
        assert ws.exists(".agent/artifacts/plan.json")
        assert ws.exists(".agent/PLAN.md")

    def test_same_phase_does_not_clear(self, tmp_path: Path) -> None:
        """Same-phase retry: is_fresh=False → files are NOT deleted."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws, "development_result",
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )

        # development → development (same-phase retry)
        clear_phase_entry_drains(ws, "development", "development", pipeline, artifacts_policy)

        # Files must still exist
        assert ws.exists(".agent/artifacts/development_result.json")
        assert ws.exists(".agent/DEVELOPMENT_RESULT.md")

    def test_fresh_entry_absent_files_no_error(self, tmp_path: Path) -> None:
        """Fresh entry with absent files: no FileNotFoundError or exception raised."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        # No files pre-created - should not raise
        clear_phase_entry_drains(ws, "planning", None, pipeline, artifacts_policy)

    def test_empty_clear_drains_list_no_exception(self, tmp_path: Path) -> None:
        """Phase with empty clear_drains_on_fresh_entry: no deletion, no exception."""
        pipeline = PipelinePolicy(
            phases={
                "empty_phase": PhaseDefinition(
                    drain="empty_phase",
                    role="execution",
                    transitions=PhaseTransition(on_success="terminal"),
                    clear_drains_on_fresh_entry=[],
                ),
                "terminal": PhaseDefinition(
                    drain="terminal",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="terminal"),
                ),
            },
            entry_phase="empty_phase",
            terminal_phase="terminal",
            recovery=RecoveryPolicy(failed_route="terminal"),
        )
        artifacts_policy = _load_default_policy_bundle()[1]

        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        # Pre-create any file
        ws.write(".agent/artifacts/plan.json", json.dumps({"type": "plan"}))

        # Empty list → nothing deleted, no exception
        clear_phase_entry_drains(ws, "empty_phase", None, pipeline, artifacts_policy)
        assert ws.exists(".agent/artifacts/plan.json")

    def test_planning_from_dev_commit_clears_planning_and_planning_analysis_drains(
        self, tmp_path: Path
    ) -> None:
        """planning enters from dev_commit, clears planning and planning_analysis drains."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws, "plan",
            ".agent/artifacts/plan.json",
            ".agent/PLAN.md",
        )
        _write_artifact_files(
            ws, "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.json",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws, "development_result",
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )

        # planning from development_commit (fresh entry, cross-phase)
        clear_phase_entry_drains(ws, "planning", "development_commit", pipeline, artifacts_policy)

        # planning and planning_analysis artifacts are cleared
        assert not ws.exists(".agent/artifacts/plan.json")
        assert not ws.exists(".agent/PLAN.md")
        assert not ws.exists(".agent/artifacts/planning_analysis_decision.json")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        # development artifact is NOT cleared (not in planning's clear_drains_on_fresh_entry)
        assert ws.exists(".agent/artifacts/development_result.json")
        assert ws.exists(".agent/DEVELOPMENT_RESULT.md")

    def test_development_from_planning_analysis_clears_analysis_dev_drains(
        self, tmp_path: Path
    ) -> None:
        """dev entering from planning_analysis clears analysis, dev, and dev_analysis drains."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws, "plan",
            ".agent/artifacts/plan.json",
            ".agent/PLAN.md",
        )
        _write_artifact_files(
            ws, "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.json",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws, "development_result",
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            ws, "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.json",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )

        # development from planning_analysis (fresh entry, cross-phase)
        clear_phase_entry_drains(ws, "development", "planning_analysis", pipeline, artifacts_policy)

        # planning artifact is NOT cleared (not in development's clear_drains_on_fresh_entry)
        assert ws.exists(".agent/artifacts/plan.json")
        assert ws.exists(".agent/PLAN.md")
        # planning_analysis, development, and development_analysis artifacts ARE cleared
        assert not ws.exists(".agent/artifacts/planning_analysis_decision.json")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not ws.exists(".agent/artifacts/development_result.json")
        assert not ws.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.json")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_development_commit_from_dev_analysis_clears_dev_drains(
        self, tmp_path: Path
    ) -> None:
        """dev_commit enters from dev_analysis, clears dev and dev_analysis drains."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws, "plan",
            ".agent/artifacts/plan.json",
            ".agent/PLAN.md",
        )
        _write_artifact_files(
            ws, "development_result",
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            ws, "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.json",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws, "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.json",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )

        # development_commit from development_analysis (fresh entry, cross-phase)
        clear_phase_entry_drains(
            ws, "development_commit", "development_analysis", pipeline, artifacts_policy
        )

        # development and development_analysis artifacts are cleared
        assert not ws.exists(".agent/artifacts/development_result.json")
        assert not ws.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.json")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")
        # planning and planning_analysis artifacts are NOT cleared
        assert ws.exists(".agent/artifacts/plan.json")
        assert ws.exists(".agent/PLAN.md")
        assert ws.exists(".agent/artifacts/planning_analysis_decision.json")
        assert ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")


# ---------------------------------------------------------------------------
# Rendered banner assertions
# ---------------------------------------------------------------------------


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

    def test_show_phase_start_renders_planning_banner_with_correct_style(
        self,
    ) -> None:
        """show_phase_start for planning renders output containing 'Planning'."""
        pipeline, _ = _load_default_policy_bundle()
        console = Console(record=True)
        ctx = make_display_context(console=console)
        show_phase_start("planning", display_context=ctx, pipeline_policy=pipeline)
        output = console.export_text()
        assert "Planning" in output
