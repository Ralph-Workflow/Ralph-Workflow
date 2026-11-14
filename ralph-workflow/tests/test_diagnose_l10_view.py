from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ralph.cli.main import app
from ralph.git.commit_result import CommitCreationResult
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_best_effort(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "ralph.git.operations.create_commit",
        lambda *a, **kw: CommitCreationResult.already_advanced("00000000"),
    )
    monkeypatch.setattr(
        "ralph.config.bootstrap.create_commit",
        lambda *a, **kw: CommitCreationResult.already_advanced("00000000"),
    )
    monkeypatch.setattr(
        "ralph.config.agents_bootstrap.create_commit",
        lambda *a, **kw: CommitCreationResult.already_advanced("00000000"),
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("HOME", str(tmp_path / ".home"))
    (tmp_path / ".config").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".home").mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "ralph@example.com")
    _git(tmp_path, "config", "user.name", "Ralph Test")
    (tmp_path / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "shared.txt")
    _git(tmp_path, "commit", "-qm", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _plain(output: str) -> str:
    return " ".join(_ANSI_ESCAPE.sub("", output).split())


def _table_text(output: str) -> str:
    cells: list[str] = []
    for line in _ANSI_ESCAPE.sub("", output).splitlines():
        if "│" not in line:
            continue
        parts = line.split("│")
        cells.extend(part.strip() for part in parts[1:-1] if part.strip())
    return " ".join(cells)


def _diagnose(workspace: Path) -> str:
    result = CliRunner().invoke(app, ["--diagnose"], catch_exceptions=False)
    assert result.exit_code in (0, 1), result.output
    return result.output


def _feature_behind_target(workspace: Path) -> None:
    _git(workspace, "checkout", "-qb", "feature")
    (workspace / "shared.txt").write_text("feature\n", encoding="utf-8")
    _git(workspace, "commit", "-qam", "feature work")
    _git(workspace, "checkout", "-q", "main")
    _git(workspace, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    (workspace / "main.txt").write_text("main\n", encoding="utf-8")
    _git(workspace, "add", "main.txt")
    _git(workspace, "commit", "-qm", "main work")
    _git(workspace, "checkout", "-q", "feature")


def _feature_ahead_of_target(workspace: Path) -> None:
    _git(workspace, "checkout", "-qb", "feature")
    (workspace / "shared.txt").write_text("feature\n", encoding="utf-8")
    _git(workspace, "commit", "-qam", "feature work")


def test_diagnose_reports_relation_strategy_reason_progress_and_risk_while_in_flight(
    workspace: Path,
) -> None:
    _git(workspace, "checkout", "-qb", "feature")
    (workspace / "shared.txt").write_text("feature\n", encoding="utf-8")
    _git(workspace, "commit", "-qam", "feature work")
    _git(workspace, "checkout", "-q", "main")
    (workspace / "shared.txt").write_text("main\n", encoding="utf-8")
    _git(workspace, "commit", "-qam", "main work")
    _git(workspace, "checkout", "-q", "feature")
    _git_best_effort(workspace, "rebase", "main")

    write_record(
        workspace,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=_git(workspace, "rev-parse", "feature"),
            pre_target_sha=_git(workspace, "rev-parse", "main"),
            resolving_rebase=True,
            resolving_paths=("shared.txt",),
        ),
    )

    output = _diagnose(workspace)
    table = _table_text(output)

    assert "Integration: in flight" in _plain(output), _plain(output)
    assert "Relation (feature vs target)" in table, table
    assert "Chosen strategy" in table, table
    assert "Why this strategy" in table, table
    assert "Done (paths resolved)" in table, table
    assert "Remaining (paths unresolved)" in table, table
    assert "At risk" in table, table
    assert "diverged" in table, table
    assert "merge" in table, table
    assert "Rebase is not the cheapest safe strategy" in table, table
    assert "shared.txt" in table, table
    assert "Remaining (paths unresolved) 1" in table, table


def test_diagnose_states_nothing_is_in_flight_without_an_integration_record(
    workspace: Path,
) -> None:
    _feature_behind_target(workspace)
    output = _diagnose(workspace)
    table = _table_text(output)
    plain = _plain(output)

    assert "nothing in flight — no integration record and no paused operation" in plain, plain
    assert "Relation (feature vs target)" in table, table
    assert "behind" in table, table
    assert "Chosen strategy" in table, table
    assert "rebase" in table, table
    assert "At risk nothing" in table, table
    assert "At risk nothing" in table, table


def test_diagnose_reports_a_feature_that_is_strictly_ahead_of_the_target(
    workspace: Path,
) -> None:
    _feature_ahead_of_target(workspace)

    output = _diagnose(workspace)
    print("AFTER DIAGNOSE LAST COMMIT:\n", _git(workspace, "log", "-n", "1"))
    table = _table_text(output)

    assert "Relation (feature vs target)" in table, table
    assert "ahead" in table, table
    assert "Chosen strategy" in table, table
    assert "fast_forward" in table, table
    assert "Feature is strictly ahead of target" in table, table
