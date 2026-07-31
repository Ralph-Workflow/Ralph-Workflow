"""Black-box subprocess tests for the deterministic AGY simulator."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.smoke_test_result import SmokeTestResult

import_module("ralph.mcp.artifacts.markdown.specs")

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_DEFAULT_ARGS = (
    "--print",
    "--dangerously-skip-permissions",
    "--model",
    "claude-sonnet-4-6",
    "hello",
)

_BATCH_DRIVER = """
import contextlib
import json
import os
import sys
from io import StringIO

from tests._support import mock_agy

cases = json.loads(sys.stdin.read())
results = {}
for case in cases:
    env_backup = os.environ.copy()
    os.environ["MOCK_AGY_BEHAVIOR"] = case["behavior"]
    os.environ["MOCK_AGY_ARTIFACT_DIR"] = case["artifact_dir"]
    stdout_buf = StringIO()
    stderr_buf = StringIO()
    try:
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            returncode = mock_agy.main(case["args"])
    finally:
        os.environ.clear()
        os.environ.update(env_backup)
    results[case["name"]] = {
        "returncode": returncode,
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "artifact_dir": case["artifact_dir"],
    }
print(json.dumps(results))
"""


@dataclass(frozen=True)
class _MockAgyCaseResult:
    returncode: int
    stdout: str
    stderr: str
    artifact_dir: Path


def _run_mock_agy_batch(
    cases: list[tuple[str, str, tuple[str, ...], Path]],
) -> dict[str, _MockAgyCaseResult]:
    payload = [
        {
            "name": name,
            "behavior": behavior,
            "args": list(args),
            "artifact_dir": str(artifact_dir),
        }
        for name, behavior, args, artifact_dir in cases
    ]
    proc = subprocess.run(
        [sys.executable, "-c", _BATCH_DRIVER],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        msg = (
            "mock AGY batch driver failed\n"
            f"stdout: {proc.stdout}\n"
            f"stderr: {proc.stderr}"
        )
        raise AssertionError(msg)
    raw = json.loads(proc.stdout)
    return {
        name: _MockAgyCaseResult(
            returncode=entry["returncode"],
            stdout=entry["stdout"],
            stderr=entry["stderr"],
            artifact_dir=Path(entry["artifact_dir"]),
        )
        for name, entry in raw.items()
    }


@pytest.fixture(scope="module")
def mock_agy_batch(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, _MockAgyCaseResult]:
    """Run every mock AGY scenario once in a single Python subprocess."""
    base = tmp_path_factory.mktemp("mock_agy_batch")
    case_dirs = {
        name: base / name
        for name in (
            "normal",
            "quota_exhausted",
            "invalid_model",
            "missing_print",
            "gemini_model",
            "bad_model",
        )
    }
    for case_dir in case_dirs.values():
        case_dir.mkdir()

    return _run_mock_agy_batch(
        [
            ("normal", "normal", _DEFAULT_ARGS, case_dirs["normal"]),
            (
                "quota_exhausted",
                "quota_exhausted",
                _DEFAULT_ARGS,
                case_dirs["quota_exhausted"],
            ),
            (
                "invalid_model",
                "invalid_model",
                _DEFAULT_ARGS,
                case_dirs["invalid_model"],
            ),
            (
                "missing_print",
                "normal",
                (
                    "--dangerously-skip-permissions",
                    "--model",
                    "claude-sonnet-4-6",
                    "hello",
                ),
                case_dirs["missing_print"],
            ),
            (
                "gemini_model",
                "normal",
                (
                    "--print",
                    "--dangerously-skip-permissions",
                    "--model",
                    "gemini-3.5-flash-low",
                    "hello",
                ),
                case_dirs["gemini_model"],
            ),
            (
                "bad_model",
                "normal",
                (
                    "--print",
                    "--dangerously-skip-permissions",
                    "--model",
                    "not-a-real-model",
                    "hello",
                ),
                case_dirs["bad_model"],
            ),
        ]
    )


def test_mock_normal_prints_and_writes_artifact(
    mock_agy_batch: dict[str, _MockAgyCaseResult],
) -> None:
    """Normal behavior emits stdout with a tool-use line and writes the artifact.

    The mock never emits a spoofable transcript completion marker. In the
    harness, ``RALPH_MCP_RUN_ID`` also makes it write the durable declaration
    sentinel; this standalone call intentionally omits that run id. The
    authoritative tool-activity signal is the
    ``[plain] tool: NAME`` line that the GenericParser classifies as
    ``type='tool_use'``.
    """
    result = mock_agy_batch["normal"]
    assert result.returncode == 0
    assert result.stdout.strip()
    lines = result.stdout.strip().splitlines()
    assert any(line == "[plain] tool: createTodoList" for line in lines), (
        f"Expected a parser-classifiable tool-use line, got: {lines!r}"
    )
    assert "Task declared complete:" not in result.stdout, (
        "Mock should NOT emit the transcript completion marker; the AGY "
        "prompt no longer asks the agent to print one and the harness must "
        "not trust it"
    )
    assert any(line.startswith("Session ID: interactive-agy-smoke-") for line in lines)
    artifact_path = result.artifact_dir / ".agent" / "tmp" / "smoke_test_result.md"
    assert artifact_path.exists()


def test_mock_quota_exhausted_returns_empty(
    mock_agy_batch: dict[str, _MockAgyCaseResult],
) -> None:
    """Quota-exhausted behavior exits 0 with empty stdout."""
    result = mock_agy_batch["quota_exhausted"]
    assert result.returncode == 0
    assert result.stdout == ""


def test_mock_invalid_model_returns_empty(
    mock_agy_batch: dict[str, _MockAgyCaseResult],
) -> None:
    """Invalid-model behavior exits 0 with empty stdout."""
    result = mock_agy_batch["invalid_model"]
    assert result.returncode == 0
    assert result.stdout == ""


def test_mock_missing_print_exits_2(
    mock_agy_batch: dict[str, _MockAgyCaseResult],
) -> None:
    """Without --print the mock exits 2 and complains on stderr."""
    result = mock_agy_batch["missing_print"]
    assert result.returncode == 2
    assert "mock AGY: --print is required" in result.stderr


def test_mock_different_canonical_model_name(
    mock_agy_batch: dict[str, _MockAgyCaseResult],
) -> None:
    """The mock accepts any published model ID from ``agy models``."""
    result = mock_agy_batch["gemini_model"]
    assert result.returncode == 0
    assert "[plain] tool: createTodoList" in result.stdout
    artifact_path = result.artifact_dir / ".agent" / "tmp" / "smoke_test_result.md"
    assert artifact_path.exists()


def test_mock_artifact_schema_validates(
    mock_agy_batch: dict[str, _MockAgyCaseResult],
) -> None:
    """The written Markdown document validates against the smoke_test_result spec."""
    result = mock_agy_batch["normal"]
    artifact_path = result.artifact_dir / ".agent" / "tmp" / "smoke_test_result.md"
    markdown = artifact_path.read_text(encoding="utf-8")
    content, diagnostics = parse_and_validate(markdown, get_spec("smoke_test_result"))
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert errors == [], f"Expected a spec-clean Markdown artifact, got: {errors}"
    validated = SmokeTestResult.model_validate(content)
    assert validated.status == "passed"
    assert validated.output_file == "tmp/interactive-agy-smoke/todo-list.js"
    assert validated.observed_breaks == []
    assert "tool activity" in validated.headless_guide_checks
    assert validated.summary


def test_mock_rejects_non_canonical_model(
    mock_agy_batch: dict[str, _MockAgyCaseResult],
) -> None:
    """A non-canonical ``--model`` is rejected (empty stdout, no artifact)."""
    result = mock_agy_batch["bad_model"]
    assert result.returncode == 0
    assert result.stdout == ""
    artifact_path = result.artifact_dir / ".agent" / "tmp" / "smoke_test_result.md"
    assert not artifact_path.exists()


def test_mock_writes_todo_list_file(
    mock_agy_batch: dict[str, _MockAgyCaseResult],
) -> None:
    """Normal behavior creates the todo-list.js output file."""
    result = mock_agy_batch["normal"]
    todo_path = result.artifact_dir / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    assert todo_path.exists()
    text = todo_path.read_text(encoding="utf-8")
    assert "function createTodoList" in text
    assert "module.exports" in text
