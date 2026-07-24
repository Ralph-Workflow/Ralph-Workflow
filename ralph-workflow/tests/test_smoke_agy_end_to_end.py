"""End-to-end assertions for the AGY smoke harness.

These tests run the user-facing ``ralph smoke-interactive-agy`` command in
a bounded subprocess against the deterministic mock AGY binary, capture the
smoke report, and assert the harness's contract surfaces. Each test creates
its own isolated workspace under ``tmp_path`` so the run is reproducible
and never depends on a preexisting repository log. The mock binary is the
always-green contract proof; the live ``agy`` binary is exercised
end-to-end by the 8-test live regression suite
(``tests/test_agy_live_regression.py``) under ``make test-live-agy``.

Policy note: this file is marked ``subprocess_e2e`` and is excluded from
the maintained ``ralph.test_suites`` invocation that ``make verify`` runs
(the 60 s combined test budget only covers the in-process unit /
integration suite). The tests below run on demand with
``uv run pytest tests/test_smoke_agy_end_to_end.py -q -m subprocess_e2e``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(60),
]


def _mock_agy_path() -> Path:
    return Path(__file__).resolve().parent / "_support" / "mock_agy.sh"


def _run_fresh_agy_smoke(
    tmp_path: Path,
    *,
    timeout_seconds: int = 120,
) -> str:
    """Drive the smoke command against the mock binary in an isolated workspace.

    The mock binary is the always-green contract proof: it writes the
    ``tmp/interactive-agy-smoke/todo-list.js`` file, the fallback
    ``.agent/tmp/smoke_test_result.md`` Markdown artifact (which the
    harness promotes to the canonical
    ``.agent/artifacts/smoke_test_result.md`` plus a receipt), and emits the
    canonical output lines (including the ``[plain] tool: createTodoList``
    line the GenericParser classifies as ``type='tool_use'`` so the
    authoritative tool-activity signal is present in the transcript).
    Returns the combined stdout + stderr (the smoke harness routes its
    log lines through loguru which defaults to stderr, so the report
    must be assembled from both streams).
    """
    mock_path = _mock_agy_path()
    env = os.environ.copy()
    env["RALPH_AGY_BINARY"] = str(mock_path)
    env["MOCK_AGY_BEHAVIOR"] = "normal"
    env["MOCK_AGY_ARTIFACT_DIR"] = str(tmp_path)
    # Unset live-binary env vars to ensure the mock wins and no other
    # test session state bleeds in.
    env.pop("AGY_BINARY", None)
    env.pop("MOCK_AGY_ARTIFACT_DIR_OVERRIDE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ralph",
            "smoke-interactive-agy",
            "--agent",
            "agy/Gemini 3.5 Flash (Medium)",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise AssertionError(
            f"smoke-interactive-agy exited with unexpected returncode "
            f"{result.returncode}; stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout + result.stderr


def test_agy_binary_is_available_in_path_or_mock_under_test() -> None:
    """Either the real ``agy`` is on ``PATH`` or the bundled mock exists.

    The substantive tests below use the mock binary so they can run in
    any environment, but the live binary being on ``PATH`` is still
    useful for the live regression suite to discover the
    ``pytest.mark.live_agy`` mark without crashing.
    """
    assert shutil.which("agy") is not None or _mock_agy_path().is_file(), (
        "Expected either a real `agy` binary on PATH or the bundled mock "
        f"AGY shell wrapper at {_mock_agy_path()}"
    )


def _read_breaks_from_report(report_text: str) -> str:
    """Pull the ``Observed breaks:`` block out of the captured smoke report."""
    lines = report_text.splitlines()
    in_breaks = False
    breaks_lines: list[str] = []
    for line in lines:
        if "Observed breaks:" in line:
            in_breaks = True
            continue
        if in_breaks:
            if not line.strip() or line.startswith("  Agent:") or "parity smoke report" in line:
                break
            stripped = line.lstrip(" -│┃")
            if stripped:
                breaks_lines.append(stripped)
    return " ".join(breaks_lines)


def test_mock_smoke_log_documents_real_agy_invocation(tmp_path: Path) -> None:
    """A freshly produced mock-backed smoke log shows a real ``agy`` invocation."""
    log_text = _run_fresh_agy_smoke(tmp_path)
    assert "Invoking agent:" in log_text, "Log does not contain an Invoking agent line"
    assert "--dangerously-skip-permissions" in log_text, (
        "Invoking line is missing --dangerously-skip-permissions"
    )
    assert "mock_agy" in log_text, (
        "Log does not show a mock-AGY invocation (the mock is the only "
        "binary used by this fresh-evidence test)"
    )

    agy_row = next(
        (line for line in log_text.splitlines() if "agy/" in line and ("│" in line or "┃" in line)),
        None,
    )
    assert agy_row is not None, "AGY parity table row not found in smoke log"

    cells = [cell.strip() for cell in re.split(r"[│┃]", agy_row) if cell.strip()]
    assert len(cells) >= 8, f"Unexpected AGY table row shape: {cells}"

    file_created = cells[2]
    breaks = _read_breaks_from_report(log_text)

    assert file_created == "yes", f"Expected file=yes on the fresh mock-backed run, got: {cells}"
    assert "AGY --print returned empty stdout" not in breaks, (
        f"Expected no upstream diagnostic on a fresh mock-backed run, got: {breaks}"
    )


def test_mock_smoke_invoking_line_uses_single_model_argv_token(tmp_path: Path) -> None:
    """The --model flag carries the canonical display name as a single argv token."""
    log_text = _run_fresh_agy_smoke(tmp_path)
    invoking_idx = log_text.find("Invoking agent:")
    assert invoking_idx != -1, "No 'Invoking agent:' line found in log"
    # The display wraps long command lines at the console width (long
    # workspace paths force a wrap), so normalize whitespace across the
    # wrapped segment instead of matching a single physical line.
    invoking_line = " ".join(log_text[invoking_idx : invoking_idx + 2000].split())

    canonical_models = (
        "Gemini 3.5 Flash (Medium)",
        "Gemini 3.5 Flash (High)",
        "Gemini 3.5 Flash (Low)",
        "Gemini 3.1 Pro (Low)",
        "Gemini 3.1 Pro (High)",
        "Claude Sonnet 4.6 (Thinking)",
        "Claude Opus 4.6 (Thinking)",
        "GPT-OSS 120B (Medium)",
    )
    matched_model = next(
        (m for m in canonical_models if f"--model {m} --print" in invoking_line),
        None,
    )
    assert matched_model is not None, (
        "AGY display name was not passed as a single argv token. "
        f"Expected one of {canonical_models!r}. Got: {invoking_line}"
    )
    skip_idx = invoking_line.find("--dangerously-skip-permissions")
    model_idx = invoking_line.find("--model")
    assert skip_idx != -1 and model_idx != -1 and skip_idx < model_idx, (
        "AGY flag order is wrong: --dangerously-skip-permissions must precede --model: "
        + invoking_line
    )


def test_mock_smoke_report_shows_text_output(tmp_path: Path) -> None:
    """The freshly produced smoke report shows parser-classified text output, not raw."""
    log_text = _run_fresh_agy_smoke(tmp_path)
    in_observed_output = False
    rendered_text_lines: list[str] = []
    raw_lines: list[str] = []
    parser_classified_lines: list[str] = []
    for line in log_text.splitlines():
        if "Observed output:" in line:
            in_observed_output = True
            continue
        if in_observed_output:
            if not line.strip():
                if rendered_text_lines or raw_lines or parser_classified_lines:
                    break
                continue
            if "Observed breaks:" in line or line.startswith("═") or line.startswith("─"):
                break
            stripped = line.lstrip(" -│┃").strip()
            if not stripped:
                continue
            if stripped.startswith("text: "):
                parser_classified_lines.append(stripped)
                continue
            if stripped.startswith("agy/"):
                if stripped.endswith(": raw"):
                    raw_lines.append(stripped)
                else:
                    rendered_text_lines.append(stripped)

    assert rendered_text_lines or parser_classified_lines, (
        "Expected at least one rendered model-text line in 'Observed output:'. "
        f"raw_lines={raw_lines} parser_classified_lines={parser_classified_lines}"
    )
    assert not raw_lines, f"Expected zero ': raw' lines in 'Observed output:', got: {raw_lines}"
