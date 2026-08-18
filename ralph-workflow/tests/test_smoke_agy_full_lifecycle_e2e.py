"""Full-lifecycle AGY smoke: parser -> harness -> MCP wire -> completion -> capability.

This is the wt-015-agy-support S-6 end-to-end proof. It drives the public
``ralph smoke-interactive-agy`` command in a bounded subprocess against the
``MOCK_AGY_V1_1_13=1`` augmented mock (see ``tests/_support/mock_agy.py``),
with a real broker secret so the mock's stdlib JSON-RPC round trips hit the
harness's actual MCP fallback server. It then asserts every cross-cutting
signal the product brief names actually reached the harness:

- the parser emitted at least 8 events covering init, step_updates,
  subagent dispatch and result;
- a verified wire-ledger record for ``ralph_submit_md_artifact`` exists for
  the run (``wire_evidence_for`` with the broker secret);
- the durable completion sentinel is present and validates via the
  documented harness helper (``_check_completion_sentinel``);
- the subagent dispatch/result pair both surfaced (the brief's named
  failure mode, "subagent dispatch was not observed");
- the display's capability recorder observed SYNTAX_HIGHLIGHTING during the
  run -- proven through the harness's own loud-failure surface: the
  ``write_to_file`` pair exercises the declared SUPPORTED
  SYNTAX_HIGHLIGHTING stance, so
  ``_detect_capability_breaks`` emits ``declared capability
  SYNTAX_HIGHLIGHTING never rendered ...`` into ``Observed breaks:`` iff the
  recorder did NOT observe it. No such break + the write_to_file tool call
  in the observed transcript is the recorded-capability proof.

The test reuses the subprocess harness shape already proven by
``tests/test_smoke_agy_end_to_end.py`` -- no new harness code is introduced.
"""

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

#: Distinct broker secret so this test's wire-ledger / sentinel HMAC
#: verification never depends on (or mutates) ambient developer state.
_E2E_BROKER_SECRET = "test-agy-full-lifecycle-e2e-broker-secret"


def _mock_agy_path() -> Path:
    return Path(__file__).resolve().parent / "_support" / "mock_agy.sh"


def _run_full_lifecycle_smoke(tmp_path: Path) -> tuple[int, str]:
    """Run ``ralph smoke-interactive-agy`` against the v1.1.13 mock; return (rc, output)."""
    mock_path = _mock_agy_path()
    assert mock_path.is_file(), f"Mock AGY script not found at {mock_path}"
    env = os.environ.copy()
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
    """The v1.1.13 mock drives every cross-cutting signal through the real CLI."""
    returncode, output = _run_full_lifecycle_smoke(tmp_path)

    assert returncode == 0, (
        f"smoke-interactive-agy exited {returncode} (expected 0 for the "
        f"always-green v1.1.13 mock contract). Output:\n{output}"
    )

    # --- parser emitted >= 8 events (init, step_updates, subagent pair, ...) ---
    event_counts = [int(count) for count in re.findall(r"parser emitted (\d+) event\(s\)", output)]
    assert event_counts, f"No 'parser emitted N event(s)' line in report:\n{output}"
    assert max(event_counts) >= 8, (
        f"Expected at least 8 parser events, got {event_counts}.\n{output}"
    )

    # --- subagent dispatch/result pair both surfaced (brief's named failure) ---
    assert "subagent dispatch observed" in output, f"Subagent dispatch was not observed.\n{output}"
    assert "subagent result observed" in output, f"Subagent result was not observed.\n{output}"

    # --- the write_to_file call was seen in the observed transcript ---
    assert "write_to_file" in output, (
        f"Expected the write_to_file tool pair in the observed output.\n{output}"
    )

    # --- SYNTAX_HIGHLIGHTING was recorded (loud-failure contrapositive) ---
    # The v1.1.13 mock's write_to_file pair exercises the declared SUPPORTED
    # SYNTAX_HIGHLIGHTING stance, so _detect_capability_breaks emits the
    # break below into "Observed breaks:" iff the display's capability
    # recorder did NOT observe the render.
    assert "SYNTAX_HIGHLIGHTING never rendered" not in output, (
        f"The display's capability recorder did not observe "
        f"SYNTAX_HIGHLIGHTING despite the write_to_file tool call.\n{output}"
    )
    assert "Verdict: PASS" in output, (
        f"Expected a PASS verdict with real wire-grade MCP round trips.\n{output}"
    )

    # --- durable, cross-process evidence in the run workspace ---
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


# --- MOCK_AGY_BEHAVIOR negative-contract selectors (wt-015-agy-support S-5) ---
#
# Each selector alters exactly one contract signal while preserving the rest
# of the lifecycle. The CLI must exit non-zero and surface the selector's
# documented diagnostic so a superficial green smoke can never mask a broken
# signal.
#
# wt-064-ccs-support S-6: each selector drives one full
# ``ralph smoke-interactive-agy`` lifecycle, and that lifecycle spawns TWO
# Python interpreters (the CLI itself plus the standalone Ralph MCP HTTP
# server subprocess whose handlers mint the receipt, sentinel, and wire
# records). ~2.5 s of every selector is therefore interpreter startup, not
# test logic, and eight serial selectors burned ~20 s of the immutable 60 s
# combined ``make test`` budget (see
# ``ralph/verify.py::_TOTAL_TEST_BUDGET_SECONDS``) -- the single largest
# line item in the slowest shard. The selectors are mutually independent
# (distinct cwd, distinct ``MOCK_AGY_ARTIFACT_DIR``, and a per-bridge
# reserved MCP port for the server subprocess), so a bounded thread pool
# fans them out without weakening a single assertion: every selector below
# still asserts the real CLI exit code and its own documented diagnostic,
# through the same real CLI -> MCP server -> mock AGY subprocess boundary
# the positive lifecycle test proves.

#: The runner gives the dedicated required-E2E shard one xdist worker. Two
#: independent CLI/MCP lifecycles can therefore overlap without competing with
#: another in-shard worker, reducing the slowest required shard while retaining
#: separate workspaces, ports, and per-selector assertions. A larger fan-out
#: previously killed a selector's server process under host load.
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
    """One selector's real-CLI result, paired with its documented diagnostics."""

    behavior: str
    returncode: int
    output: str


def _run_negative_smoke(tmp_path: Path, behavior: str) -> tuple[int, str]:
    """Run the lifecycle smoke with one MOCK_AGY_BEHAVIOR selector forced."""
    env = os.environ.copy()
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
    """Run every negative selector concurrently, each in its own workspace.

    Each behavior gets a dedicated cwd and ``MOCK_AGY_ARTIFACT_DIR`` under
    ``base_dir``, and each lifecycle reserves its own ephemeral MCP port for
    the harness's server subprocess, so the selectors share no mutable
    state. Results are returned in ``_NEGATIVE_SELECTORS`` order regardless
    of completion order, keeping assertions (and any failure report)
    deterministic.
    """
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
    """Every selector must exit non-zero with its own documented diagnostic.

    Runs the selectors concurrently (see ``_NEGATIVE_SELECTOR_FANOUT``) but
    asserts each one individually, in declaration order, against the real
    CLI -> MCP server -> mock AGY subprocess boundary: a selector passes only
    if its own exit code is non-zero AND every diagnostic it owns appears in
    that selector's captured output.
    """
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
    """Plan S-7: ``missing_completion`` leaves ZERO canonical round-trip records.

    The selector makes ``drive_real_mcp_round_trips`` short-circuit to
    ``(None, None)`` (see ``tests/_support/mock_agy_v1_1_13.py`` — the
    round trip itself is pruned, not just the frame that reports it), so
    the wire ledger must contain neither a ``ralph_submit_md_artifact``
    nor a ``declare_complete`` record: the canonical submission pipeline
    never fabricates a receipt for a round trip the mock deliberately
    skipped, while the run still exits non-zero with its own diagnostic.
    """
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
        f"missing_completion must not leave a submit wire record; ledger "
        f"tool_names={tool_names!r}"
    )
    assert "declare_complete" not in tool_names, (
        f"missing_completion must not leave a declare_complete wire record; "
        f"ledger tool_names={tool_names!r}"
    )
