"""The real-git shard's xdist fan-out never exceeds the host's cores.

The required auto-integrate shard runs with up to twelve xdist workers
alongside the general shards. On a 4-core CI runner twelve workers
oversubscribed the host three times over, so the wall clock measured
scheduler thrash rather than the suite. These tests drive
``run_test_suites`` with a stub spawner and an injected core count and
read the ``-n`` the dedicated shard was launched with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph import test_suites as test_suites_module
from tests._test_test_suites_helpers import (
    EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES,
    _FakeShardProcess,
    _StubSpawner,
)

if TYPE_CHECKING:
    from pathlib import Path


def _dedicated_shard_xdist_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cpu_count: int | None
) -> str:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)
    spawner = _StubSpawner([_FakeShardProcess([0]) for _index in range(2)])

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
    dedicated_command = spawner.calls[-1][0]
    assert sorted(spawner.manifest_files[-1]) == sorted(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    return dedicated_command[dedicated_command.index("-n") + 1]


@pytest.mark.parametrize(
    ("cpu_count", "expected_workers"),
    ((None, "1"), (1, "1"), (4, "4"), (8, "8"), (12, "12"), (40, "12")),
)
def test_real_git_shard_workers_are_capped_by_the_host_cores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    expected_workers: str,
) -> None:
    assert _dedicated_shard_xdist_workers(tmp_path, monkeypatch, cpu_count) == expected_workers
