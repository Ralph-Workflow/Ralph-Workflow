from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from ralph import test_suites as test_suites_module
from tests._test_test_suites_helpers import _FakeShardProcess, _StubSpawner

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.verify_budget_real_time,
    pytest.mark.timeout_seconds(125),
]

_BUDGET_SECONDS = 60.0
_PLUGIN_AUTOLOAD_ENV = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"


def _make_test_child_env() -> dict[str, str]:
    env = {**os.environ, "PYTEST_WORKERS": "auto"}
    env.pop(_PLUGIN_AUTOLOAD_ENV, None)
    return env


def test_make_test_child_env_keeps_plugin_autoload_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_PLUGIN_AUTOLOAD_ENV, "1")

    env = _make_test_child_env()

    assert _PLUGIN_AUTOLOAD_ENV not in env
    assert env["PYTEST_WORKERS"] == "auto"


def test_shard_child_env_keeps_plugin_autoload_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_PLUGIN_AUTOLOAD_ENV, "1")
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "0")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    spawner = _StubSpawner([_FakeShardProcess([0])])

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: ("tests/test_alpha.py", "tests/test_bravo.py"),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assert len(spawner.calls) == 1
    _command, _cwd, env = spawner.calls[0]
    assert _PLUGIN_AUTOLOAD_ENV not in env


def test_make_test_completes_within_budget() -> None:
    ralph_workflow_dir = Path(__file__).resolve().parent.parent
    start = time.monotonic()

    result = subprocess.run(
        ["make", "test"],
        cwd=str(ralph_workflow_dir),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=_make_test_child_env(),
    )

    elapsed = time.monotonic() - start

    detail_lines = [
        f"make test exit code: {result.returncode}",
        f"Elapsed: {elapsed:.2f}s (budget: {_BUDGET_SECONDS:.1f}s)",
    ]
    if result.stdout:
        detail_lines.append(f"--- STDOUT ---\n{result.stdout[-2000:]}")
    if result.stderr:
        detail_lines.append(f"--- STDERR ---\n{result.stderr[-2000:]}")
    failure_detail = "\n".join(detail_lines)

    assert result.returncode == 0, failure_detail
    assert elapsed <= _BUDGET_SECONDS, failure_detail
