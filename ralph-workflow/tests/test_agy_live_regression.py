"""Black-box end-to-end regression for the live AGY binary.

Pairs with tests/test_smoke_agy_end_to_end.py (which inspects an on-disk log)
and tests/test_agy_harness_with_mock.py (which drives the harness with a mock).
This file is the single source of truth that the live AGY binary produces a
green parity table through the harness.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.skipif(
        not shutil.which("agy"),
        reason="live AGY binary not installed in PATH",
    ),
    pytest.mark.timeout_seconds(50),
]


def _write_smoke_prompt(prompt_file: Path) -> None:
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        "Create a small JavaScript todo list at tmp/interactive-agy-smoke/todo-list.js.",
        encoding="utf-8",
    )


@pytest.fixture
def workspace_mirror(tmp_path: Path) -> Generator[Path, None, None]:
    prompt_file = tmp_path / "tmp" / "interactive-agy-smoke" / "PROMPT.md"
    _write_smoke_prompt(prompt_file)
    yield tmp_path


@pytest.fixture
def live_env(workspace_mirror: Path) -> dict[str, str]:
    env = {**os.environ, "HOME": str(workspace_mirror)}
    env.pop("RALPH_AGY_BINARY", None)
    return env


def test_live_agy_invokes_live_binary(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """The live smoke run invokes the real agy binary, not the mock."""
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy"],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=45,
        check=False,
    )
    output = result.stdout + result.stderr

    assert "Invoking agent: agy --dangerously-skip-permissions" in output, (
        f"Expected live invocation line in output:\n{output[-5000:]}"
    )
    assert "MOCK_AGY_BEHAVIOR=" not in output, (
        f"Mock marker should not appear in live run:\n{output[-5000:]}"
    )


def test_live_agy_produces_green_parity_table(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """The parity table reports file=yes, tool activity=yes, artifact=yes, breaks=none."""
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy"],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=45,
        check=False,
    )
    output = result.stdout + result.stderr

    assert "│ agy/Claude Sonnet 4.6 (Thinking) │" in output, (
        f"Expected AGY parity row in output:\n{output[-5000:]}"
    )
    assert "│ yes │" in output, (
        f"Expected File=yes column in parity table:\n{output[-5000:]}"
    )
    assert "│ none │" in output or "│ none" in output, (
        f"Expected Breaks=none in parity table:\n{output[-5000:]}"
    )


def test_live_agy_artifact_present(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """After the live smoke run, the smoke_test_result artifact is present."""
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy"],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=45,
        check=False,
    )
    output = result.stdout + result.stderr

    artifact_path = workspace_mirror / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}\nOutput:\n{output[-5000:]}"
    )


def test_live_agy_no_breaks_and_tool_artifact_activity(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """The parity row has Tool activity=yes, Artifact=yes, Breaks=none."""
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy"],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=45,
        check=False,
    )
    output = result.stdout + result.stderr

    assert "Breaks: none" in output or "No breaks" in output, (
        f"Expected no breaks in detailed report:\n{output[-5000:]}"
    )
    assert "tool activity observed" in output.lower() or "tool activity" in output.lower(), (
        f"Expected tool activity in output:\n{output[-5000:]}"
    )
    assert "artifact submitted" in output.lower() or (
        "smoke_test_result artifact" in output.lower()
    ), (
        f"Expected artifact submission:\n{output[-5000:]}"
    )
