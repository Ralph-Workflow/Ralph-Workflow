"""Tests for is_fresh_phase_entry function."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.phase_entry_cleaner import is_fresh_phase_entry
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseDefinition,
    PhaseRole,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)

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
