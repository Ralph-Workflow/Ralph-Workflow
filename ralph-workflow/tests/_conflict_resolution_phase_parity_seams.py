from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git import merge as merge_module
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.sight import ConflictSight
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from ralph.policy.models import PolicyBundle


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "_install_seams also fakes the late-imported git merge observations": (
        "the round observer imports unmerged_paths and conflict_stage_entries from ralph.git.merge at call time, so the seam spawned real git against a non-repository tmp_path on every round and pushed tests past the 1 s per-test CPU budget under host load; the fakes return exactly what git returns there (the query-failed sentinel and no stage entries), so every assertion sees the same values"
    ),
}


class _CooldownClock(FakeClock):
    def now(self) -> float:
        return self.monotonic()


_CONFLICTED = ["src/alpha.py"]


@functools.lru_cache(maxsize=1)
def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {}, "conflict_resolution": {"max_rounds_per_stop": 10}}
    )


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str] = _CONFLICTED,
    surviving_per_round: Sequence[Sequence[str]] | None = None,
) -> None:
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    monkeypatch.setattr(
        merge_module, "unmerged_paths", lambda _root: [merge_module.UNMERGED_PATH_QUERY_FAILED]
    )
    monkeypatch.setattr(merge_module, "conflict_stage_entries", lambda _root, _paths: {})
    monkeypatch.setattr(driver_module, "conflict_stage_entries", lambda _root, _paths: {})
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, paths: dict.fromkeys(paths, ConflictSight.AGENT),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _root, _kinds: ())
    remaining = list(surviving_per_round) if surviving_per_round is not None else [list(unmerged)]

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        if remaining:
            return list(remaining.pop(0))
        return list(unmerged)

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
