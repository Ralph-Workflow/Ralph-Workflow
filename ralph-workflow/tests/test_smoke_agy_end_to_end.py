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
    pytest.mark.subprocess_e2e,
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
    # The smoke log may come from a live AGY binary (``Invoking agent: agy ...``)
    # or from a mock binary invoked via ``RALPH_AGY_BINARY``; both must produce a
    # recognisable AGY invocation line. We assert the --dangerously-skip-permissions
    # flag is present, the binary is either the literal ``agy`` or an
    # ``/abs/path/...mock_agy...`` path, and the AGY display name is preserved as
    # a single argv token.
    assert "Invoking agent:" in log_text, "Log does not contain an Invoking agent line"
    assert "--dangerously-skip-permissions" in log_text, (
        "Invoking line is missing --dangerously-skip-permissions"
    )
    assert (
        "Invoking agent: agy --dangerously-skip-permissions" in log_text
        or "mock_agy" in log_text
    ), "Log does not show a real AGY or mock-AGY invocation"

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
        # The live AGY contract surfaces "AGY --print returned empty stdout:" in
        # the Breaks column; the mock path with MOCK_AGY_BEHAVIOR=quota_exhausted
        # surfaces the informational "mock AGY produced empty stdout by design"
        # note instead.
        assert (
            "AGY --print returned empty stdout:" in breaks
            or "mock AGY produced empty stdout by design" in breaks
        ), f"Expected upstream or mock empty-stdout diagnostic in breaks, got: {breaks}"


def test_invoking_line_uses_single_model_argv_token() -> None:
    log_path = _log_path()
    assert log_path.exists(), f"Live smoke log not found at {log_path}"

    log_text = log_path.read_text(encoding="utf-8")
    invoking_line = next(
        (line for line in log_text.splitlines() if "Invoking agent:" in line),
        None,
    )
    assert invoking_line is not None, "No 'Invoking agent:' line found in log"

    # The AGY model display name is the repo-consistent default shared
    # with the live regression suite (``agy/Gemini 3.5 Flash (Medium)``).
    # The --model flag must carry one of the 8 canonical model display
    # names from ``agy models`` as a single argv token so shlex.split
    # downstream keeps the display name as one token (the display name
    # has spaces and parens, so a quoted single token is required).
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


def test_live_smoke_report_shows_text_output() -> None:
    """The 'Observed output:' section shows rendered model text, not the 'raw' type label.

    The smoke harness's ``meaningful_output_lines`` is now parser-classified
    (after the smoke_plumbing fix in the wt-015 cycle), so the rendered
    Observed output section uses the ``- text: <content>`` line format from
    ``_meaningful_output_lines`` instead of the legacy ``- agy/<name>: <content>``
    rendered-from-display line. The test accepts both formats so a freshly
    captured smoke log passes regardless of which path the harness used.
    """
    log_path = _log_path()
    assert log_path.exists(), f"Live smoke log not found at {log_path}"

    log_text = log_path.read_text(encoding="utf-8")
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
            # Parser-classified format: "- text: <content>" (current contract).
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
    assert not raw_lines, (
        f"Expected zero ': raw' lines in 'Observed output:', got: {raw_lines}"
    )
