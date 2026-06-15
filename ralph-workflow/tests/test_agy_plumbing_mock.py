"""Contract tests for AGY-specific smoke plumbing.

The prompt-contract and mock-diagnostic tests are fast unit tests that run
under ``make test``. The negative import-time invariant tests (guards firing
on bad values and surviving ``python -O``) are marked ``subprocess_e2e`` and
run under ``make test-subprocess-e2e``.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ralph.config.enums import AgentTransport
from ralph.pipeline.plumbing.smoke_plumbing import (
    _AGENT_SESSION_CEILINGS,
    _SMOKE_IDLE_TIMEOUT_SECONDS,
    _SMOKE_MAX_TURNS,
    _agy_upstream_diagnostic,
    _build_smoke_prompt,
)


def test_agy_prompt_allows_agent_artifacts() -> None:
    """The AGY smoke prompt explicitly allows ``.agent/artifacts/`` writes."""
    prompt_text = _build_smoke_prompt(
        "tmp/interactive-agy-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_artifact",
        transport=AgentTransport.AGY,
    )
    assert ".agent/artifacts/" in prompt_text
    assert "Do not touch files outside the workspace-managed paths" in prompt_text
    assert "Do not touch files outside tmp/" not in prompt_text


def test_agy_prompt_forbids_other_agent_subdirectories() -> None:
    """The AGY smoke prompt forbids writes outside ``tmp/`` and ``.agent/artifacts/``."""
    prompt_text = _build_smoke_prompt(
        "tmp/interactive-agy-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_artifact",
        transport=AgentTransport.AGY,
    )
    assert "do not write to any other `.agent/` subdirectory" in prompt_text
    assert "workspace root" in prompt_text


def test_smoke_invariants_hold() -> None:
    """The smoke plumbing invariants are satisfied by the current constants."""
    assert _SMOKE_MAX_TURNS >= 1
    assert _SMOKE_IDLE_TIMEOUT_SECONDS > 0
    assert _AGENT_SESSION_CEILINGS["agy"] > _SMOKE_IDLE_TIMEOUT_SECONDS


def test_agy_mock_empty_stdout_diagnostic_is_informational(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When RALPH_AGY_BINARY is set, empty stdout surfaces an informational note."""
    monkeypatch.setenv("RALPH_AGY_BINARY", "/mock/agy")
    diagnostic = _agy_upstream_diagnostic([], tmp_path)
    assert diagnostic is not None
    assert "mock AGY produced empty stdout by design" in diagnostic
    assert "MOCK_AGY_BEHAVIOR=quota_exhausted or invalid_model" in diagnostic
    assert "harness captured this correctly" in diagnostic
    assert "individual API quota exhausted" not in diagnostic
    assert "RESOURCE_EXHAUSTED" not in diagnostic


def _get_smoke_plumbing_path() -> str:
    """Return the absolute path to ralph/pipeline/plumbing/smoke_plumbing.py."""
    test_dir = Path(__file__).parent
    return str(test_dir.parent / "ralph" / "pipeline" / "plumbing" / "smoke_plumbing.py")


def _run_patched_smoke_plumbing_import(
    *,
    max_turns: int | None = None,
    idle_timeout: float | None = None,
    agy_ceiling: float | None = None,
    minus_o: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that patches smoke_plumbing.py constants and imports it."""
    smoke_path = _get_smoke_plumbing_path()
    # smoke_path is .../ralph-workflow/ralph/pipeline/plumbing/smoke_plumbing.py
    repo_root = str(Path(smoke_path).parent.parent.parent.parent)
    original = Path(smoke_path).read_text(encoding="utf-8")

    patched = original
    if max_turns is not None:
        patched = patched.replace(
            f"_SMOKE_MAX_TURNS = {5}",
            f"_SMOKE_MAX_TURNS = {max_turns}",
        )
    if idle_timeout is not None:
        patched = patched.replace(
            f"_SMOKE_IDLE_TIMEOUT_SECONDS = {30.0}",
            f"_SMOKE_IDLE_TIMEOUT_SECONDS = {idle_timeout}",
        )
    if agy_ceiling is not None:
        patched = patched.replace(
            '"agy": 360.0,',
            f'"agy": {agy_ceiling},',
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="smoke_plumbing_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location(\n"
            "    'ralph.pipeline.plumbing.smoke_plumbing',\n"
            f"    {tmp_path!r},\n"
            ")\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "sys.modules['ralph.pipeline.plumbing.smoke_plumbing'] = mod\n"
            "spec.loader.exec_module(mod)\n"
            "print('OK')\n"
        )

        cmd = [sys.executable]
        if minus_o:
            cmd.append("-O")
        cmd.extend(["-c", runner])

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
            check=False,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()


@pytest.mark.subprocess_e2e
def test_smoke_max_turns_invariant_fires() -> None:
    """_SMOKE_MAX_TURNS < 1 must raise RuntimeError at import time."""
    result = _run_patched_smoke_plumbing_import(max_turns=0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_SMOKE_MAX_TURNS must be >= 1" in result.stderr


@pytest.mark.subprocess_e2e
def test_smoke_max_turns_invariant_survives_minus_o() -> None:
    """_SMOKE_MAX_TURNS invariant must survive ``python -O``."""
    result = _run_patched_smoke_plumbing_import(max_turns=0, minus_o=True)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_SMOKE_MAX_TURNS must be >= 1" in result.stderr


@pytest.mark.subprocess_e2e
def test_smoke_idle_timeout_invariant_fires() -> None:
    """_SMOKE_IDLE_TIMEOUT_SECONDS <= 0 must raise RuntimeError at import time."""
    result = _run_patched_smoke_plumbing_import(idle_timeout=0.0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_SMOKE_IDLE_TIMEOUT_SECONDS must be > 0" in result.stderr


@pytest.mark.subprocess_e2e
def test_smoke_idle_timeout_invariant_survives_minus_o() -> None:
    """_SMOKE_IDLE_TIMEOUT_SECONDS invariant must survive ``python -O``."""
    result = _run_patched_smoke_plumbing_import(idle_timeout=-1.0, minus_o=True)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_SMOKE_IDLE_TIMEOUT_SECONDS must be > 0" in result.stderr


@pytest.mark.subprocess_e2e
def test_agy_session_ceiling_invariant_fires() -> None:
    """AGY ceiling <= idle timeout must raise RuntimeError at import time."""
    result = _run_patched_smoke_plumbing_import(agy_ceiling=10.0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_AGENT_SESSION_CEILINGS['agy'] must exceed _SMOKE_IDLE_TIMEOUT_SECONDS" in result.stderr


@pytest.mark.subprocess_e2e
def test_agy_session_ceiling_invariant_survives_minus_o() -> None:
    """AGY ceiling invariant must survive ``python -O``."""
    result = _run_patched_smoke_plumbing_import(agy_ceiling=10.0, minus_o=True)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_AGENT_SESSION_CEILINGS['agy'] must exceed _SMOKE_IDLE_TIMEOUT_SECONDS" in result.stderr
