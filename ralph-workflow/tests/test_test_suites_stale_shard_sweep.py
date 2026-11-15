from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from ralph import test_suites as test_suites_module
from tests._test_test_suites_helpers import _FakeShardProcess, _StubSpawner

_SEVEN_HOURS = 7 * 60 * 60


def _age(path: Path, seconds: int) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _shard_root(parent: Path, name: str) -> Path:
    root = parent / name
    shard = root / "shard-0"
    shard.mkdir(parents=True)
    (shard / "leftover.txt").write_text("x", encoding="utf-8")
    return root


def _run_one_shard() -> int:
    return test_suites_module.run_test_suites(
        cwd=Path.cwd(),
        spawner=_StubSpawner([_FakeShardProcess([0])]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )


def test_a_killed_verification_leaves_no_shard_root_behind_on_the_next_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    parent = tmp_path / "ralph-pytest-shards"
    abandoned = _shard_root(parent, "verification-abandoned")
    _age(abandoned / "shard-0", _SEVEN_HOURS)
    _age(abandoned, _SEVEN_HOURS)
    in_flight = _shard_root(parent, "verification-in-flight")

    assert _run_one_shard() == 0

    assert not abandoned.exists()
    assert in_flight.exists()


def test_a_shard_root_with_recent_shard_activity_is_kept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    parent = tmp_path / "ralph-pytest-shards"
    long_running = _shard_root(parent, "verification-long-running")
    _age(long_running, _SEVEN_HOURS)

    assert _run_one_shard() == 0

    assert long_running.exists()
