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
    def test_previous_phase_none_is_fresh(self) -> None:
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("planning", None, pipeline) is True

    def test_same_phase_is_not_fresh(self) -> None:
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("planning", "planning", pipeline) is False
        assert is_fresh_phase_entry("development", "development", pipeline) is False

    def test_analysis_loopback_is_not_fresh(self) -> None:
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("planning", "planning_analysis", pipeline) is False
        assert is_fresh_phase_entry("development", "development_analysis", pipeline) is False

    def test_unrelated_cross_phase_is_fresh(self) -> None:
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("development", "planning_analysis", pipeline) is True
        assert is_fresh_phase_entry("planning", "development_commit", pipeline) is True

    def test_unknown_previous_phase_is_fresh(self) -> None:
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("planning", "nonexistent_phase", pipeline) is True

    def test_development_commit_from_development_is_fresh(self) -> None:
        pipeline, _ = _load_default_policy_bundle()
        assert is_fresh_phase_entry("development_commit", "development", pipeline) is True
