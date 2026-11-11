from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from ralph.agents.completion_signals import _check_completion_sentinel
from ralph.mcp.server._wire_ledger import WIRE_LEDGER_RELPATH, wire_evidence_for
from ralph.pipeline.plumbing.smoke_plumbing import resolve_smoke_harness_spec

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(45),
]

_AGENT = "agy/gemini-3.6-flash-low"

_E2E_BROKER_SECRET = "test-agy-full-lifecycle-e2e-broker-secret"


def _mock_agy_path() -> Path:
    return Path(__file__).resolve().parent / "_support" / "mock_agy.sh"


def _run_full_lifecycle_smoke(tmp_path: Path) -> tuple[int, str]:
    mock_path = _mock_agy_path()
    assert mock_path.is_file(), f"Mock AGY script not found at {mock_path}"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["RALPH_AGY_BINARY"] = str(mock_path)
    env["MOCK_AGY_BEHAVIOR"] = "normal"
    env["MOCK_AGY_ARTIFACT_DIR"] = str(tmp_path)
    env["MOCK_AGY_V1_1_13"] = "1"
    env["RALPH_BROKER_SECRET"] = _E2E_BROKER_SECRET
    env.pop("MCP_AUTH_TOKEN", None)
    env.pop("MOCK_AGY_SUBAGENT", None)
    env.pop("AGY_BINARY", None)
    env.pop("MOCK_AGY_ARTIFACT_DIR_OVERRIDE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ralph",
            "smoke-interactive-agy",
            "--agent",
            _AGENT,
            "--subagents",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def test_agy_full_lifecycle_e2e(tmp_path: Path) -> None:
    returncode, output = _run_full_lifecycle_smoke(tmp_path)

    assert returncode == 0, (
        f"smoke-interactive-agy exited {returncode} (expected 0 for the "
        f"always-green v1.1.13 mock contract). Output:\n{output}"
    )

    event_counts = [int(count) for count in re.findall(r"parser emitted (\d+) event\(s\)", output)]
    assert event_counts, f"No 'parser emitted N event(s)' line in report:\n{output}"
    assert max(event_counts) >= 8, (
        f"Expected at least 8 parser events, got {event_counts}.\n{output}"
    )

    assert "subagent dispatch observed" in output, f"Subagent dispatch was not observed.\n{output}"
    assert "subagent result observed" in output, f"Subagent result was not observed.\n{output}"

    assert "write_to_file" in output, (
        f"Expected the write_to_file tool pair in the observed output.\n{output}"
    )

    assert "SYNTAX_HIGHLIGHTING never rendered" not in output, (
        f"The display's capability recorder did not observe "
        f"SYNTAX_HIGHLIGHTING despite the write_to_file tool call.\n{output}"
    )
    assert "Verdict: PASS" in output, (
        f"Expected a PASS verdict with real wire-grade MCP round trips.\n{output}"
    )

    run_id = resolve_smoke_harness_spec(_AGENT).run_id
    assert (
        wire_evidence_for(
            tmp_path,
            run_id,
            tool_name="ralph_submit_md_artifact",
            secret=_E2E_BROKER_SECRET,
        )
        is True
    ), (
        f"Expected a verified wire-ledger tools/call record for "
        f"ralph_submit_md_artifact on run_id={run_id!r} under {tmp_path}"
    )
    assert (
        _check_completion_sentinel(
            tmp_path,
            run_id,
            sentinel_secret=_E2E_BROKER_SECRET,
        )
        is True
    ), (
        f"Expected the durable, HMAC-verified completion sentinel for "
        f"run_id={run_id!r} under {tmp_path}"
    )


_NEGATIVE_SELECTOR_FANOUT = 2

_NEGATIVE_SELECTORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("no_output", ("AGY --print returned empty stdout",)),
    ("malformed_stream", ("no tool activity was observed",)),
    ("failed_result", ("result frame reported status=FAILED",)),
    ("missing_dispatch", ("subagent dispatch was not observed",)),
    ("missing_result", ("subagent result was not observed",)),
    ("missing_artifact", ("smoke_test_result artifact was not submitted",)),
    ("missing_completion", ("completion sentinel was not observed",)),
)


@dataclass(frozen=True)
class _NegativeSelectorOutcome:
    behavior: str
    returncode: int
    output: str


def _run_negative_smoke(tmp_path: Path, behavior: str) -> tuple[int, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["RALPH_AGY_BINARY"] = str(_mock_agy_path())
    env["MOCK_AGY_BEHAVIOR"] = behavior
    env["MOCK_AGY_ARTIFACT_DIR"] = str(tmp_path)
    env["MOCK_AGY_V1_1_13"] = "1"
    env["RALPH_BROKER_SECRET"] = _E2E_BROKER_SECRET
    env.pop("MCP_AUTH_TOKEN", None)
    env.pop("MOCK_AGY_SUBAGENT", None)
    env.pop("AGY_BINARY", None)
    env.pop("MOCK_AGY_ARTIFACT_DIR_OVERRIDE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ralph",
            "smoke-interactive-agy",
            "--agent",
            _AGENT,
            "--subagents",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def _run_negative_selectors_in_parallel(
    base_dir: Path,
) -> list[_NegativeSelectorOutcome]:
    for behavior, _diagnostics in _NEGATIVE_SELECTORS:
        (base_dir / behavior).mkdir()
    with ThreadPoolExecutor(
        max_workers=_NEGATIVE_SELECTOR_FANOUT,
        thread_name_prefix="agy-neg",
    ) as pool:
        futures = {
            behavior: pool.submit(_run_negative_smoke, base_dir / behavior, behavior)
            for behavior, _diagnostics in _NEGATIVE_SELECTORS
        }
    return [
        _NegativeSelectorOutcome(behavior, *futures[behavior].result())
        for behavior, _diagnostics in _NEGATIVE_SELECTORS
    ]


def test_agy_full_lifecycle_e2e_negative_selectors_fail_loudly(
    tmp_path: Path,
) -> None:
    outcomes = _run_negative_selectors_in_parallel(tmp_path)

    assert len(outcomes) == len(_NEGATIVE_SELECTORS)
    for outcome, (behavior, diagnostics) in zip(
        outcomes,
        _NEGATIVE_SELECTORS,
        strict=True,
    ):
        assert outcome.behavior == behavior
        assert outcome.returncode != 0, (
            f"smoke-interactive-agy with MOCK_AGY_BEHAVIOR={outcome.behavior} "
            f"exited 0 (a non-zero exit is required so a superficial green "
            f"cannot mask the broken signal). Output:\n{outcome.output}"
        )
        for diagnostic in diagnostics:
            assert diagnostic in outcome.output, (
                f"MOCK_AGY_BEHAVIOR={outcome.behavior} must surface "
                f"{diagnostic!r}.\n{outcome.output}"
            )


def test_missing_completion_selector_produces_zero_wire_receipts(tmp_path: Path) -> None:
    returncode, output = _run_negative_smoke(tmp_path, "missing_completion")

    assert returncode != 0, f"missing_completion must exit non-zero.\n{output}"
    assert "completion sentinel was not observed" in output, (
        f"missing_completion must surface its documented diagnostic.\n{output}"
    )

    ledger_path = tmp_path / WIRE_LEDGER_RELPATH
    tool_names: set[object] = set()
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("method") == "tools/call":
                tool_names.add(record.get("tool_name"))
    assert "ralph_submit_md_artifact" not in tool_names, (
        f"missing_completion must not leave a submit wire record; ledger tool_names={tool_names!r}"
    )
    assert "declare_complete" not in tool_names, (
        f"missing_completion must not leave a declare_complete wire record; "
        f"ledger tool_names={tool_names!r}"
    )
