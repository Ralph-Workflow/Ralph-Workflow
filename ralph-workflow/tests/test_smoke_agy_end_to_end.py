"""End-to-end assertions for the live AGY smoke run log.

These tests inspect the captured log from ``ralph smoke-interactive-agy``
without invoking a real subprocess, network call, or sleep. They fail if the
smoke harness ever fabricates a success message that is not backed by a real
AGY invocation, or if the AGY display name is split into multiple argv tokens.

Policy note (2026-06-14): this file was previously unmarked and ran as part
of ``make test``. It inspects real subprocess output written by a separate
``ralph smoke-interactive-agy`` invocation, which violates the policy
"non-subprocess_e2e tests must not use real file I/O" the audit enforces for
the regular suite. The log file lives outside pytest's ``tmp_path`` fixture
(``tmp/smoke-interactive-agy-run.log`` is a real on-disk artefact produced
by a prior run), so the test is necessarily environment-coupled. Marked
``subprocess_e2e`` to exclude it from the timed test-cov run; the targeted
``make test-subprocess-e2e`` target remains the right invocation.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not shutil.which("agy"),
        reason="AGY binary not installed in PATH",
    ),
]


def _log_path() -> Path:
    return Path(__file__).resolve().parents[1] / "tmp" / "smoke-interactive-agy-run.log"


def test_agy_binary_is_available_in_path() -> None:
    assert shutil.which("agy") is not None


def _read_breaks_from_report(log_text: str) -> str:
    lines = log_text.splitlines()
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


def test_live_smoke_log_documents_real_agy_invocation() -> None:
    log_path = _log_path()
    assert log_path.exists(), f"Live smoke log not found at {log_path}"

    log_text = log_path.read_text(encoding="utf-8")
    assert "Invoking agent: agy --dangerously-skip-permissions" in log_text, (
        "Log does not show a real AGY invocation"
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

    if file_created == "yes":
        assert "AGY --print returned empty stdout" not in breaks, (
            f"Expected no upstream diagnostic when file=yes, got: {breaks}"
        )
    else:
        assert file_created == "no", f"Expected file=no or file=yes, got: {cells}"
        assert "AGY --print returned empty stdout:" in breaks, (
            f"Expected actionable upstream diagnostic in breaks, got: {breaks}"
        )


def test_invoking_line_uses_single_model_argv_token() -> None:
    log_path = _log_path()
    assert log_path.exists(), f"Live smoke log not found at {log_path}"

    log_text = log_path.read_text(encoding="utf-8")
    invoking_line = next(
        (line for line in log_text.splitlines() if "Invoking agent:" in line),
        None,
    )
    assert invoking_line is not None, "No 'Invoking agent:' line found in log"

    assert "--model Claude Sonnet 4.6 (Thinking) --print" in invoking_line, (
        "AGY display name was not passed as a single argv token: " + invoking_line
    )
    skip_idx = invoking_line.find("--dangerously-skip-permissions")
    model_idx = invoking_line.find("--model")
    assert skip_idx != -1 and model_idx != -1 and skip_idx < model_idx, (
        "AGY flag order is wrong: --dangerously-skip-permissions must precede --model: "
        + invoking_line
    )
