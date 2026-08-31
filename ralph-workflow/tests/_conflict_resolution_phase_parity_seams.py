"""Shared seams for the conflict-resolution chain-parity suites.

The parity tests all drive the real
:func:`~ralph.pipeline.conflict_resolution.driver.run_conflict_resolution_pipeline`
against the shipped policy bundle, with only the two git queries its
verdict rests on replaced. Those four pieces live here so
``test_conflict_resolution_phase_parity`` and
``test_conflict_resolution_attempt_attribution`` script the driver
identically -- a divergence between them would make the two files
disagree about what the pipeline was even asked to do.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from ralph.policy.models import PolicyBundle


_CONFLICTED = ["src/alpha.py"]


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {}})


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str] = _CONFLICTED,
    surviving_per_round: Sequence[Sequence[str]] | None = None,
) -> None:
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    remaining = list(surviving_per_round) if surviving_per_round is not None else [list(unmerged)]

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        if remaining:
            return list(remaining.pop(0))
        return list(unmerged)

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
