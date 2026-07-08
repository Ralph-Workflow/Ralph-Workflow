"""Black-box end-to-end regression for the live AGY binary.

Pairs with tests/test_smoke_agy_end_to_end.py (which inspects an on-disk log)
and tests/test_agy_harness_with_mock.py (which drives the harness with a mock).
This file is the single source of truth that the live AGY binary produces a
green parity table through the harness.

Sizing contract
---------------

The file is marked ``subprocess_e2e`` AND ``live_agy``. The
``test-subprocess-e2e`` Makefile target excludes ``live_agy`` so the
deterministic subprocess suite stays under the 60s wall-clock cap. The
live tests run under the dedicated ``test-live-agy`` target with a
per-suite timeout sized for real network-bound AGY invocations.

Within this file, the strict and observational tests share a
session-scoped fixture that runs the full smoke harness EXACTLY ONCE
per pytest session. Sharing the smoke output across tests keeps the
live-AGY subprocess usage to one invocation per session, so the
``test-live-agy`` target can finish within its per-suite timeout even
when the live binary is healthy.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import run_pty_and_read_lines
from ralph.agents.invoke._agent_run_ctx import _AgentRunCtx
from ralph.agents.timeout_clock import SystemClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.pipeline.plumbing.smoke_plumbing import resolve_smoke_harness_spec

# Capture the real HOME at import time, BEFORE the conftest
# ``_isolate_process_home`` autouse fixture remaps it to a per-test sandbox.
# AGY v1.0.9 needs the real HOME to find its keyring credentials; the
# sandbox HOME has no .gemini/antigravity-cli state and no OAuth tokens, so
# AGY falls back to the OAuth browser flow and times out.
_REAL_HOME = Path(os.environ.get("HOME") or Path.home()).resolve()

# The model alias to drive for live tests. The default ``agy/Claude Sonnet 4.6
# (Thinking)`` alias sometimes hits the 24h individual quota (RESOURCE_EXHAUSTED
# 429) in the test environment; ``agy/Gemini 3.5 Flash (Medium)`` is a
# deterministic fallback that ships with a generous per-account quota and is
# one of the 8 canonical aliases returned by ``agy models``.
_LIVE_AGY_AGENT = "agy/Gemini 3.5 Flash (Medium)"
# The expected canonical-receipt run_id mirrors the smoke harness spec for
# ``_LIVE_AGY_AGENT``. Computing it from the spec keeps the receipt test
# aligned when the fallback model changes.
_LIVE_AGY_EXPECTED_RUN_ID = resolve_smoke_harness_spec(_LIVE_AGY_AGENT).run_id


def _quick_policy() -> object:
    return TimeoutPolicy(
        idle_timeout_seconds=45.0,
        process_exit_wait_seconds=15.0,
    )


pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.live_agy,
    pytest.mark.skipif(
        not shutil.which("agy"),
        reason="live AGY binary not installed in PATH",
    ),
    pytest.mark.timeout_seconds(300),
]


def _write_smoke_prompt(prompt_file: Path) -> None:
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        "Create a small JavaScript todo list at tmp/interactive-agy-smoke/todo-list.js.",
        encoding="utf-8",
    )


def _build_live_env() -> dict[str, str]:
    """Build the env dict for a live AGY smoke run.

    AGY v1.0.9 stores its keyring credentials in the OS keychain but the
    Go runtime also keeps a session cache under ``$HOME/.gemini/antigravity-cli``.
    The conftest ``_isolate_process_home`` autouse fixture remaps HOME to a
    per-test sandbox, which makes AGY fall back to the OAuth browser flow
    and time out (the ``Print mode: auth timed out`` state recorded in
    ``cli.log``).

    We therefore override HOME in the env returned here to the user's real
    home (captured from the parent process before the autouse fixture ran)
    so the live AGY can authenticate via the keychain. The test outputs
    (``.agent/artifacts/smoke_test_result.json``, ``.agent/receipts/...``)
    are still isolated to the workspace dir because the smoke CLI derives
    ``workspace_root`` from ``Path.cwd()`` (the test's ``cwd=`` argument),
    not from ``$HOME``.
    """
    real_home = _REAL_HOME
    env = {
        **os.environ,
        "HOME": str(real_home),
        "XDG_CONFIG_HOME": str(real_home / ".config"),
    }
    repo_root = Path(__file__).resolve().parent.parent
    existing_pythonpath = env.get("PYTHONPATH")
    repo_pythonpath = str(repo_root) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    env["PYTHONPATH"] = repo_pythonpath
    env.pop("RALPH_AGY_BINARY", None)
    return env


def _read_cli_log_tail(home: Path) -> str:
    """Read the last 4096 bytes of ``~/.gemini/antigravity-cli/cli.log``."""
    cli_log_path = home / ".gemini" / "antigravity-cli" / "cli.log"
    if not cli_log_path.is_file():
        return ""
    try:
        return cli_log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
    except OSError:
        return "<unreadable>"


@dataclass(frozen=True)
class _LiveSmokeResult:
    """Captured live-smoke output shared across the session-scoped tests.

    The session-scoped fixture runs the full ``smoke-interactive-agy`` harness
    EXACTLY ONCE and exposes the result via this dataclass. The 7 strict and
    observational tests in this file all read from this fixture so the
    live-AGY subprocess is invoked once per pytest session.
    """

    output: str
    cli_log_tail: str
    workspace: Path
    expected_run_id: str


@pytest.fixture
def workspace_mirror(tmp_path: Path) -> Generator[Path, None, None]:
    prompt_file = tmp_path / "tmp" / "interactive-agy-smoke" / "PROMPT.md"
    _write_smoke_prompt(prompt_file)
    yield tmp_path


@pytest.fixture
def live_env(workspace_mirror: Path) -> dict[str, str]:
    """Per-test env for live runs that need their own workspace (e.g. PTY test)."""
    return _build_live_env()


@pytest.fixture(scope="session")
def live_smoke_session() -> Generator[_LiveSmokeResult, None, None]:
    """Session-scoped live AGY smoke run shared by the strict/observational tests.

    Runs the full smoke harness ONCE per pytest session, in a stable
    ``tempfile.TemporaryDirectory``-backed workspace. Tests that need the
    smoke output (parity table, artifact file, receipt, no-breaks,
    text-classified lines) read from this fixture instead of running
    their own smoke. The 5 strict tests and 2 observational tests share
    this single invocation, so the live-AGY subprocess usage stays at
    one run per session.

    The PTY-drain test (``test_live_agy_pty_read_thread_sees_output``)
    bypasses this fixture and uses ``run_pty_and_read_lines`` directly
    because it tests a different seam (the PTY read thread, not the
    smoke harness).
    """
    workspace = Path(tempfile.mkdtemp(prefix="agy-live-smoke-"))
    prompt_file = workspace / "tmp" / "interactive-agy-smoke" / "PROMPT.md"
    _write_smoke_prompt(prompt_file)

    env = _build_live_env()

    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy", "--agent", _LIVE_AGY_AGENT],
        capture_output=True,
        text=True,
        cwd=workspace,
        env=env,
        timeout=240,
        check=False,
    )
    output = result.stdout + result.stderr
    cli_log_tail = _read_cli_log_tail(_REAL_HOME)

    yield _LiveSmokeResult(
        output=output,
        cli_log_tail=cli_log_tail,
        workspace=workspace,
        expected_run_id=_LIVE_AGY_EXPECTED_RUN_ID,
    )


def _xfail_if_upstream_blocked(cli_log_tail: str) -> None:
    """Convert a documented upstream-blocked run into a clear pytest.xfail.

    Called by every strict and observational test before asserting the
    live contract. When the cli.log shows a documented upstream-blocked
    reason (auth timed out, not logged in, RESOURCE_EXHAUSTED 429,
    model-invalid, model-not-in-config, INVALID_ARGUMENT 400, stream
    reset), the test xfails with a clear reason so the executor records
    the real env state without a false pass. When the env is healthy,
    the function returns and the assertion runs.
    """
    upstream_reason = _detect_upstream_blocked_reason(cli_log_tail)
    if upstream_reason is not None:
        pytest.xfail(
            f"Live AGY is upstream-blocked ({upstream_reason}); "
            "the strict live assertions cannot be observed in this env. "
            "The mock-binary test test_agy_smoke_promotes_artifact_to_canonical_receipt "
            "is the always-green regression-proof. cli.log tail: "
            f"{cli_log_tail[-200:]!r}"
        )


def test_live_agy_invokes_live_binary(live_smoke_session: _LiveSmokeResult) -> None:
    """The live smoke run invokes the real agy binary, not the mock.

    Reads the session-shared smoke output (one live invocation per
    session, see ``live_smoke_session`` fixture) and asserts the
    ``Invoking agent: agy --dangerously-skip-permissions`` line is
    present and the ``MOCK_AGY_BEHAVIOR=`` marker is absent. This
    proves the harness used the real binary, not the mock.
    """
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    output = live_smoke_session.output

    assert "Invoking agent: agy --dangerously-skip-permissions" in output, (
        f"Expected live invocation line in output:\n{output[-5000:]}"
    )
    assert "MOCK_AGY_BEHAVIOR=" not in output, (
        f"Mock marker should not appear in live run:\n{output[-5000:]}"
    )


def test_live_agy_produces_green_parity_table(
    live_smoke_session: _LiveSmokeResult,
) -> None:
    """The parity table reports file=yes, tool activity=yes, artifact=yes, breaks=none.

    Reads the session-shared smoke output and asserts the AGY parity row
    includes the model alias, the ``agy`` transport column, ``yes`` for
    file creation, and ``none`` for breaks. There is NO auth/quota
    permits allowance; the upstream-blocked xfail gate converts
    documented transient conditions into a clear xfail.
    """
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    output = live_smoke_session.output
    cli_log_tail = live_smoke_session.cli_log_tail

    assert "agy/Gemini 3.5 Flash (Medium)" in output, (
        f"Expected AGY parity row in output. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )
    assert "│ agy " in output or "│ agy/" in output, (
        f"Expected AGY transport column in parity table. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert re.search(r"│\s*yes\s*│", output) is not None, (
        f"Expected File=yes column in parity table. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert re.search(r"│\s*none\s*│", output) is not None, (
        f"Expected Breaks=none in parity table. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )


def test_live_agy_artifact_present(live_smoke_session: _LiveSmokeResult) -> None:
    """After the live smoke run, the smoke_test_result artifact is present.

    Reads the session-shared smoke workspace and asserts
    ``.agent/artifacts/smoke_test_result.json`` is on disk. There is NO
    auth/quota permits allowance; the upstream-blocked xfail gate
    converts documented transient conditions into a clear xfail.
    """
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    cli_log_tail = live_smoke_session.cli_log_tail
    output = live_smoke_session.output

    artifact_path = live_smoke_session.workspace / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )


def test_live_agy_no_breaks_and_tool_artifact_activity(
    live_smoke_session: _LiveSmokeResult,
) -> None:
    """The parity row has Tool activity=yes, Artifact=yes, Breaks=none.

    Reads the session-shared smoke output and asserts the EXACT positive
    success markers in the parity report (``- tool activity observed``,
    ``- smoke_test_result artifact submitted``, ``No breaks observed``)
    AND the absence of the corresponding negative break markers. The
    marker substrings overlap, so substring-only checks would let an
    upstream-blocked run pass falsely; the xfail gate prevents that.
    """
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    output = live_smoke_session.output
    cli_log_tail = live_smoke_session.cli_log_tail

    assert "- tool activity observed" in output, (
        f"Expected the dash-prefixed '- tool activity observed' success marker. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert "- smoke_test_result artifact submitted" in output, (
        f"Expected the dash-prefixed '- smoke_test_result artifact submitted' "
        f"success marker. cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert "No breaks observed" in output or "Breaks: none" in output, (
        f"Expected no breaks marker in parity report. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )

    forbidden_negative_markers = (
        "- no tool activity was observed",
        "- smoke_test_result artifact was not submitted",
        "- expected todo-list.js was not created",
        "- declare_complete marker was not observed",
        "- no parser events were observed",
        "- fewer than 3 meaningful output lines were observed",
    )
    present_negatives = [m for m in forbidden_negative_markers if m in output]
    assert not present_negatives, (
        f"Expected the parity report to contain no break markers, found: "
        f"{present_negatives}. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )

    text_lines = re.findall(r"- text: [^\n]+", output) or []
    assert any(line.startswith("- text:") for line in text_lines), (
        "Expected at least one - text: line in detailed report. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )


def test_live_agy_pty_read_thread_sees_output(
    workspace_mirror: Path,
) -> None:
    """The PTY read thread in PtyLineReader must actually see real AGY output.

    Authoritative live-output proof: drive the live ``agy`` binary through the
    PUBLIC ``run_pty_and_read_lines`` API (not the private ``_PtyExtras``) and
    assert at least one yielded line contains the literal text ``hello`` from
    the canonical "Reply with exactly the word: hello" prompt.

    This test does NOT use the session-shared ``live_smoke_session``
    fixture: it tests a different seam (the PTY read thread, not the
    smoke harness). The per-suite 60s cap on ``test-live-agy`` is
    sized to accommodate this one direct PTY invocation.
    """
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    ctx = _AgentRunCtx(
        config=config,
        show_progress=False,
        extra_env={
            "HOME": str(_REAL_HOME),
            "XDG_CONFIG_HOME": str(_REAL_HOME / ".config"),
        },
        workspace_path=workspace_mirror,
        policy=_quick_policy(),
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        pre_output_listener=None,
        monitor=None,
        required_artifact=None,
        clock=SystemClock(),
        evaluate_completion_fn=None,
        expected_session_id=None,
        context=None,
    )
    cmd = [
        shutil.which("agy") or "agy",
        "--dangerously-skip-permissions",
        "--model",
        "Gemini 3.5 Flash (Medium)",
        "--print",
        "Reply with exactly the word: hello",
    ]

    deadline = time.monotonic() + 120.0
    yielded: list[str] = []
    saw_hello = False
    for line in run_pty_and_read_lines(cmd, ctx):
        yielded.append(line)
        if "hello" in line.lower():
            saw_hello = True
            break
        if time.monotonic() >= deadline:
            break

    cli_log_tail = _read_cli_log_tail(_REAL_HOME)

    if saw_hello:
        return

    upstream_reason = _detect_upstream_blocked_reason(cli_log_tail)
    if upstream_reason is not None:
        pytest.xfail(
            f"Live AGY --print is upstream-blocked ({upstream_reason}); "
            "the canonical 'hello' response cannot be observed. "
            "Re-run when the upstream condition clears. "
            f"cli.log tail: {cli_log_tail[-200:]!r}"
        )

    raise AssertionError(
        "PTY read thread yielded 0 lines OR no line contained the canonical "
        "'hello' substring. Drain fix is broken: the kernel's PTY buffer was "
        f"not drained after child exit. yielded={len(yielded)} lines. "
        f"cli.log tail: {cli_log_tail[-200:]!r}"
    )


_UPSTREAM_BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"Print mode:\s*auth timed out",
        "AGY --print auth timed out (no OAuth credentials in test env)",
    ),
    (
        r"You are not logged into Antigravity",
        "AGY not logged into Antigravity (no OAuth credentials in test env)",
    ),
    (
        r"RESOURCE_EXHAUSTED \(code 429\)",
        "RESOURCE_EXHAUSTED (code 429) - API quota exhausted",
    ),
    (
        r"Failed to resolve model flag\s+([^:]+):\s*model\s+(\S+)\s+is not recognized",
        "Failed to resolve model flag - model ID is not recognized",
    ),
    (
        r"Model ID\s+(\S+)\s+not in local config",
        "Model ID not in local config",
    ),
    (
        r"INVALID_ARGUMENT \(code 400\).*assistant message prefill",
        "INVALID_ARGUMENT (code 400) - model does not support assistant prefill",
    ),
    (
        r"connection reset by peer",
        "stream reading error: connection reset by peer",
    ),
)


def _detect_upstream_blocked_reason(cli_log_tail: str) -> str | None:
    """Return the first documented upstream-blocked reason found in cli.log, else None.

    Patterns are pinned to ``tmp/agy-source-of-truth.txt`` and the
    ``_AGY_QUOTA_PATTERN`` / ``_AGY_MODEL_INVALID_PATTERN`` /
    ``_AGY_MODEL_NOT_IN_CONFIG_PATTERN`` constants in
    ``ralph/pipeline/plumbing/smoke_plumbing.py:130-141``. The additional
    patterns (INVALID_ARGUMENT 400, assistant prefill, stream reset,
    auth timed out, not logged in) cover the empirical 2026-06-16 dev-machine
    cli.log evidence; they document the live-blocked state and are not part of
    the published AGY contract.
    """
    if not cli_log_tail:
        return None
    for pattern, reason in _UPSTREAM_BLOCKED_PATTERNS:
        flags = re.IGNORECASE | re.DOTALL if ".*" in pattern else re.IGNORECASE
        if re.search(pattern, cli_log_tail, flags):
            return reason
    return None


def test_live_agy_artifact_promoted_to_canonical_receipt(
    live_smoke_session: _LiveSmokeResult,
) -> None:
    """The AGY-side direct file write is promoted to a canonical receipt.

    Reads the session-shared smoke workspace and asserts the canonical
    artifact submission path: the live AGY writes
    ``.agent/artifacts/smoke_test_result.json`` directly and the
    ``promote_fallback_artifact`` / ``write_artifact_receipt`` machinery
    in ``ralph.mcp.artifacts`` durably stamps a canonical receipt keyed
    on ``(run_id, artifact_type)``. Under RFC-013 P3 that receipt lives
    in the per-workspace ``.agent/state.db`` (one row per
    ``(run_id, artifact_type)``); the legacy
    ``.agent/receipts/<run_id>/<artifact_type>.json`` file path is
    read-only fallback during the dual-read rollout window.

    Asserting via the public ``artifact_receipt_present`` read API
    verifies the behavioral promotion contract without coupling to
    which physical store the receipt landed in.

    There is NO auth/quota permits allowance; the upstream-blocked xfail
    gate converts documented transient conditions into a clear xfail.
    """
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    cli_log_tail = live_smoke_session.cli_log_tail
    output = live_smoke_session.output

    artifact_path = live_smoke_session.workspace / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )

    expected_run_id = live_smoke_session.expected_run_id
    # RFC-013 P3: assert the canonical receipt is durably present via
    # the public artifact_receipt_present read API. The legacy
    # .agent/receipts/<run_id>/<type>.json path is read-only fallback;
    # production writes go to the per-workspace .agent/state.db only.
    assert (
        artifact_receipt_present(
            live_smoke_session.workspace,
            expected_run_id,
            "smoke_test_result",
        )
        is True
    ), (
        f"Expected a canonical receipt for run_id={expected_run_id!r} "
        f"artifact_type='smoke_test_result'. Artifact present: "
        f"{artifact_path.is_file()}. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )


@pytest.mark.timeout_seconds(240)
def test_live_agy_produces_parser_classified_text_and_canonical_receipt(
    live_smoke_session: _LiveSmokeResult,
) -> None:
    """End-to-end live-binary proof: parser-classified text output AND canonical receipt.

    Combines the two contract surfaces the user explicitly asked for
    into one test that reads the session-shared smoke output: the
    harness drives the live AGY to produce non-empty
    parser-classified text output AND the canonical artifact submission
    chain (artifact + receipt) completes successfully.

    Uses the existing xfail gate via ``_detect_upstream_blocked_reason``
    (``_UPSTREAM_BLOCKED_PATTERNS``) to convert a documented
    upstream-blocked run into a clear xfail with the env state. When
    the env is healthy the test is STRICT and asserts the full contract
    end-to-end.

    The companion tests cover the individual surfaces; this test is
    the single place that proves the full live path works end-to-end
    using the session-shared smoke invocation.
    """
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    cli_log_tail = live_smoke_session.cli_log_tail
    output = live_smoke_session.output

    expected_run_id = live_smoke_session.expected_run_id
    artifact_path = live_smoke_session.workspace / ".agent" / "artifacts" / "smoke_test_result.json"

    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    # RFC-013 P3: the canonical receipt store is the per-workspace
    # .agent/state.db. Asserting via the public artifact_receipt_present
    # read API verifies the end-to-end promotion contract without
    # coupling to which physical store the receipt landed in (the legacy
    # .agent/receipts/<run_id>/<type>.json path is read-only fallback).
    assert (
        artifact_receipt_present(
            live_smoke_session.workspace,
            expected_run_id,
            "smoke_test_result",
        )
        is True
    ), (
        f"Expected canonical receipt for run_id={expected_run_id!r} "
        f"artifact_type='smoke_test_result'. Artifact present: "
        f"{artifact_path.is_file()}. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )

    text_lines = re.findall(r"- text: [^\n]+", output) or []
    assert any(line.startswith("- text:") for line in text_lines), (
        "Expected at least one - text: line in detailed report (parser-classified "
        "output). cli.log tail: "
        f"{cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )

    assert "- tool activity observed" in output, (
        "Expected the dash-prefixed '- tool activity observed' success marker. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert "- smoke_test_result artifact submitted" in output, (
        "Expected the dash-prefixed '- smoke_test_result artifact submitted' "
        f"success marker. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )


def test_live_agy_environment_diagnostic_records_upstream_block(
    live_smoke_session: _LiveSmokeResult,
) -> None:
    """Non-gating companion test that records the live AGY environment state.

    Reads the session-shared smoke output and inspects the cli.log tail
    for the documented upstream-blocked patterns (auth timed out, not
    logged in, RESOURCE_EXHAUSTED 429, model-invalid, INVALID_ARGUMENT
    400, stream reset).

    When an upstream-blocked reason is detected, the test xfails with a
    clear reason so the executor records the real environment state
    without affecting the strict-test outcomes. When the environment
    is healthy (no upstream-blocked reason in cli.log), the test passes
    — this is the diagnostic signal that the strict tests should be
    passing too.
    """
    cli_log_tail = live_smoke_session.cli_log_tail
    output = live_smoke_session.output

    upstream_reason = _detect_upstream_blocked_reason(cli_log_tail)
    if upstream_reason is not None:
        pytest.xfail(
            f"Live AGY environment diagnostic: upstream-blocked ({upstream_reason}); "
            "strict live tests in this file will fail. "
            f"cli.log tail: {cli_log_tail[-200:]!r}"
        )

    assert cli_log_tail, (
        f"Expected cli.log to be present at "
        f"{_REAL_HOME / '.gemini' / 'antigravity-cli' / 'cli.log'} when no "
        f"upstream-blocked reason is detected. Output:\n{output[-2000:]}"
    )
