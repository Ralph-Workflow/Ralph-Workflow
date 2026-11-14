from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph import test_suites as test_suites_module
from tests._test_test_suites_helpers import (
    EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES as BASE_REQUIRED_AUTO_INTEGRATE_E2E_FILES,
)
from tests._test_test_suites_helpers import (
    _FakeShardProcess,
    _StubSpawner,
)

EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES = (
    *BASE_REQUIRED_AUTO_INTEGRATE_E2E_FILES,
    "tests/scenarios/test_auto_integrate_symptom_matrix.py",
    "tests/scenarios/test_lifecycle_relations.py",
    "tests/scenarios/test_lifecycle_work_shapes.py",
    "tests/scenarios/test_lifecycle_midflight.py",
    "tests/scenarios/test_lifecycle_restart.py",
    "tests/scenarios/test_lifecycle_operator_interleave.py",
    "tests/scenarios/test_lifecycle_target_moves.py",
    "tests/scenarios/test_lifecycle_conflict_kinds.py",
    "tests/scenarios/test_lifecycle_publication_cleanup.py",
    "tests/scenarios/test_lifecycle_content_equivalence.py",
    "tests/scenarios/test_lifecycle_fault_injection.py",
)

if TYPE_CHECKING:
    from pathlib import Path


def _dedicated_lane_shard_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    expected_shards: int,
) -> int:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)
    spawner = _StubSpawner([_FakeShardProcess([0]) for _index in range(1 + expected_shards)])

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            *EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES,
            "tests/test_alpha.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    lane_manifests = spawner.manifest_files[1:]
    lane_files = tuple(path for manifest in lane_manifests for path in manifest)
    assert sorted(lane_files) == sorted(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    assert len(lane_files) == len(set(lane_files))
    return len(lane_manifests)


@pytest.mark.parametrize(
    ("cpu_count", "expected_shards"),
    ((None, 1), (1, 1), (4, 4), (8, 6), (12, 6), (40, 6)),
)
def test_real_git_lane_shards_are_capped_by_the_host_cores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    expected_shards: int,
) -> None:
    assert (
        _dedicated_lane_shard_count(tmp_path, monkeypatch, cpu_count, expected_shards)
        == expected_shards
    )
