"""Black-box subprocess tests for the deterministic AGY simulator."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts.smoke_test_result import SmokeTestResult

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


def _run_mock_agy(
    *args: str,
    behavior: str = "normal",
    artifact_dir: Path,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "MOCK_AGY_BEHAVIOR": behavior,
        "MOCK_AGY_ARTIFACT_DIR": str(artifact_dir),
    }
    return subprocess.run(
        [sys.executable, "-m", "tests._support.mock_agy", *args],
        env=env,
        timeout=10,
        capture_output=True,
        text=True,
        check=False,
    )


def test_mock_normal_prints_and_writes_artifact(tmp_path: Path) -> None:
    """Normal behavior emits stdout ending with the completion marker."""
    result = _run_mock_agy(
        "--print",
        "--dangerously-skip-permissions",
        "--model",
        "Claude Sonnet 4.6 (Thinking)",
        "hello",
        artifact_dir=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "Task declared complete:"
    assert any(line.startswith("Session ID: interactive-agy-smoke-") for line in lines)
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.exists()


def test_mock_quota_exhausted_returns_empty(tmp_path: Path) -> None:
    """Quota-exhausted behavior exits 0 with empty stdout."""
    result = _run_mock_agy(
        "--print",
        "--dangerously-skip-permissions",
        "--model",
        "Claude Sonnet 4.6 (Thinking)",
        "hello",
        behavior="quota_exhausted",
        artifact_dir=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_mock_invalid_model_returns_empty(tmp_path: Path) -> None:
    """Invalid-model behavior exits 0 with empty stdout."""
    result = _run_mock_agy(
        "--print",
        "--dangerously-skip-permissions",
        "--model",
        "Claude Sonnet 4.6 (Thinking)",
        "hello",
        behavior="invalid_model",
        artifact_dir=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_mock_missing_print_exits_2(tmp_path: Path) -> None:
    """Without --print the mock exits 2 and complains on stderr."""
    result = _run_mock_agy(
        "--dangerously-skip-permissions",
        "--model",
        "Claude Sonnet 4.6 (Thinking)",
        "hello",
        artifact_dir=tmp_path,
    )
    assert result.returncode == 2
    assert "mock AGY: --print is required" in result.stderr


def test_mock_different_canonical_model_name(tmp_path: Path) -> None:
    """The mock accepts any canonical display name from ``agy models``."""
    result = _run_mock_agy(
        "--print",
        "--dangerously-skip-permissions",
        "--model",
        "Gemini 3.5 Flash (Low)",
        "hello",
        artifact_dir=tmp_path,
    )
    assert result.returncode == 0
    assert "Task declared complete:" in result.stdout
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.exists()


def test_mock_artifact_schema_validates(tmp_path: Path) -> None:
    """The written artifact parses through SmokeTestResult.model_validate."""
    _run_mock_agy(
        "--print",
        "--dangerously-skip-permissions",
        "--model",
        "Claude Sonnet 4.6 (Thinking)",
        "hello",
        artifact_dir=tmp_path,
    )
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    content = raw["content"]
    validated = SmokeTestResult.model_validate(content)
    assert validated.status == "passed"
    assert validated.output_file == "tmp/interactive-agy-smoke/todo-list.js"
    assert validated.observed_breaks == []
    assert "tool activity" in validated.headless_guide_checks
    assert validated.summary


def test_mock_writes_todo_list_file(tmp_path: Path) -> None:
    """Normal behavior creates the todo-list.js output file."""
    _run_mock_agy(
        "--print",
        "--dangerously-skip-permissions",
        "--model",
        "Claude Sonnet 4.6 (Thinking)",
        "hello",
        artifact_dir=tmp_path,
    )
    todo_path = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    assert todo_path.exists()
    text = todo_path.read_text(encoding="utf-8")
    assert "function createTodoList" in text
    assert "module.exports" in text
