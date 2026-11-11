from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.server._wire_ledger import wire_evidence_for
from ralph.pipeline.plumbing.smoke_plumbing import resolve_smoke_harness_spec

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(60),
]


def _mock_agy_path() -> Path:
    return Path(__file__).resolve().parent / "_support" / "mock_agy.sh"


_WIRE_BROKER_SECRET = "test-agy-end-to-end-wire-broker-secret"


def _run_wire_round_trip_smoke(tmp_path: Path, *, timeout_seconds: int = 25) -> tuple[int, str]:
    mock_path = _mock_agy_path()
    assert mock_path.is_file(), f"Mock AGY script not found at {mock_path}"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["RALPH_AGY_BINARY"] = str(mock_path)
    env["MOCK_AGY_BEHAVIOR"] = "normal"
    env["MOCK_AGY_ARTIFACT_DIR"] = str(tmp_path)
    env["MOCK_AGY_V1_1_13"] = "1"
    env["RALPH_BROKER_SECRET"] = _WIRE_BROKER_SECRET
    env.pop("MCP_AUTH_TOKEN", None)
    env.pop("AGY_BINARY", None)
    env.pop("MOCK_AGY_ARTIFACT_DIR_OVERRIDE", None)
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(Path(__file__).resolve().parents[1]),
            "python",
            "-m",
            "ralph",
            "smoke-interactive-agy",
            "--agent",
            "agy/gemini-3.6-flash-low",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def _run_fresh_agy_smoke(
    tmp_path: Path,
    *,
    timeout_seconds: int = 120,
) -> str:
    mock_path = _mock_agy_path()
    assert mock_path.is_file(), f"Mock AGY script not found at {mock_path}"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["RALPH_AGY_BINARY"] = str(mock_path)
    env["MOCK_AGY_BEHAVIOR"] = "normal"
    env["MOCK_AGY_ARTIFACT_DIR"] = str(tmp_path)
    env.pop("AGY_BINARY", None)
    env.pop("MOCK_AGY_ARTIFACT_DIR_OVERRIDE", None)
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(Path(__file__).resolve().parents[1]),
            "python",
            "-m",
            "ralph",
            "smoke-interactive-agy",
            "--agent",
            "agy/gemini-3.6-flash-low",
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
    assert shutil.which("agy") is not None or _mock_agy_path().is_file(), (
        "Expected either a real `agy` binary on PATH or the bundled mock "
        f"AGY shell wrapper at {_mock_agy_path()}"
    )


def _read_breaks_from_report(report_text: str) -> str:
    lines = report_text.splitlines()
    in_breaks = False
    breaks_lines: list[str] = []
    for raw_line in lines:
        line = normalize_vt_text(raw_line)
        if "Observed breaks:" in line:
            in_breaks = True
            continue
        if in_breaks:
            if not line.strip() or line.startswith("  Agent:") or "parity smoke report" in line:
                break
            stripped = line.lstrip(" -│┃").strip()
            if stripped:
                breaks_lines.append(stripped)
    return " ".join(breaks_lines)


def test_mock_smoke_normal_selector_produces_both_wire_ledger_records(
    tmp_path: Path,
) -> None:
    returncode, output = _run_wire_round_trip_smoke(tmp_path)
    assert returncode == 0, (
        f"smoke-interactive-agy exited {returncode} (expected 0 for the "
        f"always-green normal selector). Output:\n{output}"
    )
    run_id = resolve_smoke_harness_spec("agy/gemini-3.6-flash-low").run_id
    assert (
        wire_evidence_for(
            tmp_path,
            run_id,
            tool_name="ralph_submit_md_artifact",
            secret=_WIRE_BROKER_SECRET,
        )
        is True
    ), f"No verified ralph_submit_md_artifact wire record for run_id={run_id!r}.\n{output}"
    assert (
        wire_evidence_for(
            tmp_path,
            run_id,
            tool_name="declare_complete",
            secret=_WIRE_BROKER_SECRET,
        )
        is True
    ), f"No verified declare_complete wire record for run_id={run_id!r}.\n{output}"


def test_mock_smoke_log_documents_real_agy_invocation(tmp_path: Path) -> None:
    log_text = _run_fresh_agy_smoke(tmp_path)
    assert "Invoking agent:" in log_text, "Log does not contain an Invoking agent line"
    assert "--dangerously-skip-permissions" in log_text, (
        "Invoking line is missing --dangerously-skip-permissions"
    )
    assert "mock_agy" in "".join(log_text.split()), (
        "Log does not show a mock-AGY invocation (the mock is the only "
        "binary used by this fresh-evidence test)"
    )

    agy_row = next(
        (
            line
            for line in log_text.splitlines()
            if ("│" in line or "┃" in line)
            and len([c for c in re.split(r"[│┃]", line) if c.strip()]) >= 8
            and "yes" in line
        ),
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
    log_text = _run_fresh_agy_smoke(tmp_path)
    invoking_idx = log_text.find("Invoking agent:")
    assert invoking_idx != -1, "No 'Invoking agent:' line found in log"
    invoking_line = " ".join(log_text[invoking_idx : invoking_idx + 2000].split())

    canonical_models = (
        "gemini-3.7-flash-medium",
        "gemini-3.7-flash-high",
        "gemini-3.7-flash-low",
        "gemini-3.1-pro-low",
        "gemini-3.1-pro-high",
        "claude-sonnet-5-5-high",
        "claude-opus-5-5-high",
        "gpt-oss-120b-medium",
        "gemini-3.6-flash-low",
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
    log_text = _run_fresh_agy_smoke(tmp_path)
    in_observed_output = False
    rendered_text_lines: list[str] = []
    raw_lines: list[str] = []
    parser_classified_lines: list[str] = []
    for raw_line in log_text.splitlines():
        line = normalize_vt_text(raw_line)
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


def test_mock_agy_evidence_ceiling_grades_below_wire() -> None:
    import json

    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.pipeline.plumbing.smoke_evidence import Provenance
    from ralph.pipeline.plumbing.smoke_plumbing import transport_evidence_ceiling
    from tests._support.mock_agy import _MOCK_INIT_TOOL_NAMES

    init_line = json.dumps(
        {
            "event": "init",
            "conversation_id": "00000000-0000-0000-0000-000000000000",
            "init": {
                "model": "default",
                "cwd": ".",
                "tools": list(_MOCK_INIT_TOOL_NAMES),
                "permission_mode": "always-proceed",
            },
        }
    )
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)

    ceiling = transport_evidence_ceiling(config, [init_line])

    assert ceiling == Provenance.TRANSCRIPT
    assert ceiling < Provenance.WIRE
