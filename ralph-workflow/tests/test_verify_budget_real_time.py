from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import test_suites as test_suites_module
from ralph.executor.process import ProcessResult
from ralph.verify import run_verify
from tests._test_test_suites_helpers import _FakeShardProcess, _StubSpawner

if TYPE_CHECKING:
    from collections.abc import Sequence

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "the oracle's make test honours the hang cap run_verify hands it": (
        "the real make test now runs under the timeout run_verify gives the make test step "
        "(the 225 s hang cap, three times the raised 75 s wall budget) instead of a fixed "
        "120 s that a loaded host could exceed before the budget verdict; the module budget "
        "rises from 125 to 240 s so its wall hang cap stays above that timeout, and the "
        "verdict assertions are unchanged"
    ),
    "test_make_test_completes_within_budget judges the run with the make verify budget verdict": (
        "the oracle still runs one real make test and still fails when it is over budget, but "
        "it now runs it through ralph.verify.run_verify with every other step stubbed, so the "
        "verdict uses the gate's own wall budget net of the host-contention credit and its CPU "
        "budget instead of a hardcoded 60 s raw wall clock that failed whenever other "
        "pipelines loaded the host"
    ),
}

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.verify_budget_real_time,
    pytest.mark.timeout_seconds(240),
]

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


def _run_only_make_test(
    command: str,
    args: Sequence[str] = (),
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
) -> ProcessResult:
    del capture_output
    invocation = (command, *args)
    if invocation != ("make", "test"):
        return ProcessResult(command=invocation, returncode=0, stdout="", stderr="")
    completed = subprocess.run(
        list(invocation),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**_make_test_child_env(), **(env or {})},
    )
    return ProcessResult(
        command=invocation,
        returncode=completed.returncode,
        stdout=completed.stdout[-2000:],
        stderr=completed.stderr[-2000:],
    )


def test_make_test_completes_within_budget(capsys: pytest.CaptureFixture[str]) -> None:
    ralph_workflow_dir = Path(__file__).resolve().parent.parent

    exit_code = run_verify(cwd=ralph_workflow_dir, runner=_run_only_make_test)

    captured = capsys.readouterr()
    assert exit_code == 0, f"--- STDOUT ---\n{captured.out}\n--- STDERR ---\n{captured.err}"
    assert "Combined test budget: wall " in captured.out
