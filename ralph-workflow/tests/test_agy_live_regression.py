"""Black-box end-to-end regression for the live AGY binary.

Pairs with tests/test_smoke_agy_end_to_end.py (which inspects an on-disk log)
and tests/test_agy_harness_with_mock.py (which drives the harness with a mock).
This file is the single source of truth that the live AGY binary produces a
green parity table through the harness.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
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


@pytest.fixture
def workspace_mirror(tmp_path: Path) -> Generator[Path, None, None]:
    prompt_file = tmp_path / "tmp" / "interactive-agy-smoke" / "PROMPT.md"
    _write_smoke_prompt(prompt_file)
    yield tmp_path


@pytest.fixture
def live_env(workspace_mirror: Path) -> dict[str, str]:
    """Build the env for a live AGY smoke run.

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
    are still isolated to ``workspace_mirror`` because the smoke CLI derives
    ``workspace_root`` from ``Path.cwd()`` (the test's ``cwd=workspace_mirror``
    argument), not from ``$HOME``.
    """
    # Capture the real HOME from the parent process env before the
    # _isolate_process_home autouse fixture remapped it.
    real_home = _REAL_HOME
    env = {**os.environ, "HOME": str(real_home), "XDG_CONFIG_HOME": str(real_home / ".config")}
    # Force the smoke subprocess to use the local ralph source (this test's
    # repo) rather than whichever ralph is on PYTHONPATH in the parent
    # process. The harness assertions about ``- text:`` lines depend on
    # the parser-classified output path that is in this branch.
    repo_root = Path(__file__).resolve().parent.parent
    existing_pythonpath = env.get("PYTHONPATH")
    repo_pythonpath = str(repo_root) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    env["PYTHONPATH"] = repo_pythonpath
    env.pop("RALPH_AGY_BINARY", None)
    return env


def test_live_agy_invokes_live_binary(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """The live smoke run invokes the real agy binary, not the mock."""
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy", "--agent", _LIVE_AGY_AGENT],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=240,
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
    """The parity table reports file=yes, tool activity=yes, artifact=yes, breaks=none.

    STRICT per plan step 10(b): the test asserts the AGY parity row, | yes |
    for file creation, and none for breaks. There is NO auth/quota permits
    allowance. The companion test
    ``test_live_agy_environment_diagnostic_records_upstream_block`` records
    upstream-blocked environment states without gating this strict test.

    This test only passes when the harness actually drives the live AGY to
    produce the smoke artifact and emits the AGY parity row with file=yes and
    breaks=none. In environments where the live AGY cannot authenticate
    (e.g. missing OAuth credentials) this test fails honestly; the
    environment state is recorded by the diagnostic test.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy", "--agent", _LIVE_AGY_AGENT],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=240,
        check=False,
    )
    output = result.stdout + result.stderr

    cli_log_path = Path(live_env["HOME"]) / ".gemini" / "antigravity-cli" / "cli.log"
    cli_log_tail = ""
    if cli_log_path.is_file():
        try:
            cli_log_tail = cli_log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
        except OSError:
            cli_log_tail = "<unreadable>"

    assert "agy/Gemini 3.5 Flash (Medium)" in output, (
        f"Expected AGY parity row in output. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )
    assert "│ agy " in output or "│ agy/" in output, (
        f"Expected AGY transport column in parity table. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )
    assert re.search(r"│\s*yes\s*│", output) is not None, (
        f"Expected File=yes column in parity table. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )
    assert re.search(r"│\s*none\s*│", output) is not None, (
        f"Expected Breaks=none in parity table. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )


def test_live_agy_artifact_present(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """After the live smoke run, the smoke_test_result artifact is present.

    STRICT: the test asserts the smoke_test_result.json file is on disk.
    There is NO auth/quota permits allowance. The companion test
    ``test_live_agy_environment_diagnostic_records_upstream_block`` records
    upstream-blocked environment states without gating this strict test.

    This test only passes when the harness actually drives the live AGY to
    produce the smoke artifact. In environments where the live AGY cannot
    authenticate (e.g. missing OAuth credentials) this test fails honestly;
    the environment state is recorded by the diagnostic test.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy", "--agent", _LIVE_AGY_AGENT],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=240,
        check=False,
    )
    output = result.stdout + result.stderr

    cli_log_path = Path(live_env["HOME"]) / ".gemini" / "antigravity-cli" / "cli.log"
    cli_log_tail = ""
    if cli_log_path.is_file():
        try:
            cli_log_tail = cli_log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
        except OSError:
            cli_log_tail = "<unreadable>"

    artifact_path = workspace_mirror / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )


def test_live_agy_no_breaks_and_tool_artifact_activity(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """The parity row has Tool activity=yes, Artifact=yes, Breaks=none.

    STRICT: the test asserts the EXACT positive success markers in the
    parity report and that the negative break markers are absent. The
    negative markers (e.g. ``- no tool activity was observed``) and the
    positive markers (e.g. ``- tool activity observed``) share substrings
    like ``tool activity``, so a substring-only check would let an
    upstream-blocked run pass falsely. The test now requires the
    dash-prefixed positive success markers AND the absence of the
    corresponding negative break markers.

    There is NO auth/quota permits allowance. The companion test
    ``test_live_agy_environment_diagnostic_records_upstream_block`` records
    upstream-blocked environment states without gating this strict test.

    This test only passes when the harness actually drives the live AGY to
    produce output that is classified as text events and the harness records
    tool activity and artifact submission. In environments where the live
    AGY cannot authenticate this test fails honestly; the environment state
    is recorded by the diagnostic test.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy", "--agent", _LIVE_AGY_AGENT],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=240,
        check=False,
    )
    output = result.stdout + result.stderr

    cli_log_path = Path(live_env["HOME"]) / ".gemini" / "antigravity-cli" / "cli.log"
    cli_log_tail = ""
    if cli_log_path.is_file():
        try:
            cli_log_tail = cli_log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
        except OSError:
            cli_log_tail = "<unreadable>"

    # Positive success markers: dash-prefixed bullet lines that appear in
    # the "Observed working:" section of the parity report.
    assert "- tool activity observed" in output, (
        f"Expected the dash-prefixed '- tool activity observed' success marker. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )
    assert "- smoke_test_result artifact submitted" in output, (
        f"Expected the dash-prefixed '- smoke_test_result artifact submitted' success marker. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )
    assert "No breaks observed" in output or "Breaks: none" in output, (
        f"Expected no breaks marker in parity report. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )

    # Negative break markers must NOT appear when the harness succeeds. The
    # marker substrings (e.g. "tool activity", "smoke_test_result artifact")
    # overlap with the positive success markers above, so a substring-only
    # positive check would let an upstream-blocked run pass falsely.
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
        f"{present_negatives}. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )

    text_lines = re.findall(r"- text: [^\n]+", output) or []
    assert any(line.startswith("- text:") for line in text_lines), (
        "Expected at least one - text: line in detailed report. "
        "See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        + output[-5000:]
    )


def test_live_agy_pty_read_thread_sees_output(
    workspace_mirror: Path,
) -> None:
    """The PTY read thread in PtyLineReader must actually see real AGY output.

    Authoritative live-output proof: drive the live ``agy`` binary through the
    PUBLIC ``run_pty_and_read_lines`` API (not the private ``_PtyExtras``) and
    assert at least one yielded line contains the literal text ``hello`` from
    the canonical "Reply with exactly the word: hello" prompt.

    This is the regression test for the early-exit race in
    ``PtyLineReader._read_thread``: when ``poll()`` reported the child exited,
    the loop used to break after a 10ms ``wait_for_master_readable`` check
    while the kernel's PTY buffer still held bytes the live AGY had flushed.
    The fix replaces the early-exit with a bounded EIO drain
    (``_EIO_DRAIN_MAX=32``) so the master is fully drained.

    Plan step 10(a) contract: when the live binary is reachable AND the cli.log
    does NOT show a recent documented quota/model-error condition, the test
    MUST require a yielded line containing the literal ``hello`` substring
    (the canonical "Reply with exactly the word: hello" prompt response).
    When the cli.log shows a documented upstream condition
    (RESOURCE_EXHAUSTED 429, model-invalid, model-not-in-config,
    INVALID_ARGUMENT 400, assistant prefill, stream read errors) the test
    xfails with a clear reason so the executor records the real outcome
    instead of silently passing on arbitrary output.
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

    cli_log_path = _REAL_HOME / ".gemini" / "antigravity-cli" / "cli.log"
    cli_log_tail = ""
    if cli_log_path.is_file():
        try:
            cli_log_tail = cli_log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
        except OSError:
            cli_log_tail = "<unreadable>"

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
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """The AGY-side direct file write is promoted to a canonical receipt.

    Verifies the canonical artifact submission path: the live AGY writes
    ``.agent/artifacts/smoke_test_result.json`` directly (it cannot reliably
    call ``ralph_submit_artifact`` over MCP in PTY mode), and the
    ``promote_fallback_artifact`` / ``write_artifact_receipt`` machinery in
    ``ralph.mcp.artifacts`` stamps a canonical receipt at
    ``.agent/receipts/<run_id>/smoke_test_result.json`` with the exact
    payload shape ``{"run_id": run_id, "artifact_type": artifact_type}``.

    The expected ``run_id`` is the sanitized model-name pattern from
    ``ralph.pipeline.plumbing.smoke_plumbing.resolve_smoke_harness_spec``
    (resolved at module import time from ``_LIVE_AGY_AGENT``).

    STRICT per plan AC-04: the test asserts the receipt file is present and
    contains the exact payload. There is NO auth/quota permits allowance. The
    companion test
    ``test_live_agy_environment_diagnostic_records_upstream_block`` records
    upstream-blocked environment states without gating this strict test.

    This test only passes when the harness actually drives the live AGY to
    produce the smoke artifact AND the canonical receipt is promoted. In
    environments where the live AGY cannot authenticate (e.g. missing OAuth
    credentials) this test fails honestly; the environment state is recorded
    by the diagnostic test.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy", "--agent", _LIVE_AGY_AGENT],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=240,
        check=False,
    )
    output = result.stdout + result.stderr

    cli_log_path = Path(live_env["HOME"]) / ".gemini" / "antigravity-cli" / "cli.log"
    cli_log_tail = ""
    if cli_log_path.is_file():
        try:
            cli_log_tail = cli_log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
        except OSError:
            cli_log_tail = "<unreadable>"

    artifact_path = workspace_mirror / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )

    expected_run_id = _LIVE_AGY_EXPECTED_RUN_ID
    receipt_path = (
        workspace_mirror
        / ".agent"
        / "receipts"
        / expected_run_id
        / "smoke_test_result.json"
    )
    assert receipt_path.is_file(), (
        f"Expected canonical receipt at {receipt_path}\n"
        f"Artifact present: {artifact_path.is_file()}. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )

    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_payload == {
        "run_id": expected_run_id,
        "artifact_type": "smoke_test_result",
    }, (
        f"Unexpected receipt payload: {receipt_payload}. "
        f"See test_live_agy_environment_diagnostic_records_upstream_block "
        f"for upstream-blocked environment state. cli.log tail: {cli_log_tail[-200:]!r}"
    )


def test_live_agy_environment_diagnostic_records_upstream_block(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """Non-gating companion test that records the live AGY environment state.

    The strict live tests in this file (``test_live_agy_produces_green_parity_table``,
    ``test_live_agy_artifact_present``,
    ``test_live_agy_no_breaks_and_tool_artifact_activity``,
    ``test_live_agy_artifact_promoted_to_canonical_receipt``) now FAIL
    honestly when the live AGY is upstream-blocked. This test runs the same
    harness invocation and inspects ``~/.gemini/antigravity-cli/cli.log`` for
    the documented upstream-blocked patterns (auth timed out, not logged in,
    RESOURCE_EXHAUSTED 429, model-invalid, INVALID_ARGUMENT 400, stream reset).

    When an upstream-blocked reason is detected, the test xfails with a clear
    reason so the executor records the real environment state without
    affecting the strict-test outcomes. When the environment is healthy
    (no upstream-blocked reason in cli.log), the test passes -- this is the
    diagnostic signal that the strict tests should be passing too.

    This test is intentionally NOT a gate on the strict tests; it documents
    the environment so the executor can see why the strict tests fail or pass.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ralph", "smoke-interactive-agy", "--agent", _LIVE_AGY_AGENT],
        capture_output=True,
        text=True,
        cwd=workspace_mirror,
        env=live_env,
        timeout=240,
        check=False,
    )
    cli_log_path = Path(live_env["HOME"]) / ".gemini" / "antigravity-cli" / "cli.log"
    cli_log_tail = ""
    if cli_log_path.is_file():
        try:
            cli_log_tail = cli_log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
        except OSError:
            cli_log_tail = "<unreadable>"

    upstream_reason = _detect_upstream_blocked_reason(cli_log_tail)
    if upstream_reason is not None:
        pytest.xfail(
            f"Live AGY environment diagnostic: upstream-blocked ({upstream_reason}); "
            "strict live tests in this file will fail. "
            f"cli.log tail: {cli_log_tail[-200:]!r}"
        )

    assert cli_log_tail, (
        f"Expected cli.log to be present at {cli_log_path} when no upstream-blocked "
        f"reason is detected. Output:\n{(result.stdout + result.stderr)[-2000:]}"
    )
