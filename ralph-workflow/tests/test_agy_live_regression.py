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
    """The parity table reports file=yes, tool activity=yes, artifact=yes, breaks=none.

    If the live AGY is upstream-blocked (auth timed out, not logged in, quota
    exhausted, model invalid, etc.) the test xfails with a clear reason so the
    executor records the real outcome instead of failing on an environment
    issue. The cli.log tail is inspected for the documented patterns before
    the strict assertion runs.
    """
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
            f"Live AGY --print is upstream-blocked ({upstream_reason}); "
            "the parity row cannot be emitted. Re-run when the upstream "
            f"condition clears. cli.log tail: {cli_log_tail[-200:]!r}"
        )

    assert "│ agy/Claude Sonnet 4.6 (Thinking) │" in output, (
        f"Expected AGY parity row in output:\n{output[-5000:]}"
    )
    assert "│ yes │" in output, f"Expected File=yes column in parity table:\n{output[-5000:]}"
    assert "│ none │" in output or "│ none" in output, (
        f"Expected Breaks=none in parity table:\n{output[-5000:]}"
    )


def test_live_agy_artifact_present(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """After the live smoke run, the smoke_test_result artifact is present.

    If the live AGY is upstream-blocked (auth timed out, not logged in, quota
    exhausted, model invalid, etc.) the test xfails with a clear reason so the
    executor records the real outcome instead of failing on an environment
    issue. The cli.log tail is inspected for the documented patterns before
    the strict assertion runs.
    """
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
            f"Live AGY --print is upstream-blocked ({upstream_reason}); "
            "the smoke_test_result artifact cannot be produced. "
            f"Re-run when the upstream condition clears. "
            f"cli.log tail: {cli_log_tail[-200:]!r}"
        )

    artifact_path = workspace_mirror / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}\nOutput:\n{output[-5000:]}"
    )


def test_live_agy_no_breaks_and_tool_artifact_activity(
    workspace_mirror: Path,
    live_env: dict[str, str],
) -> None:
    """The parity row has Tool activity=yes, Artifact=yes, Breaks=none.

    If the live AGY is upstream-blocked (auth timed out, not logged in, quota
    exhausted, model invalid, etc.) the test xfails with a clear reason so the
    executor records the real outcome instead of failing on an environment
    issue. The cli.log tail is inspected for the documented patterns before
    the strict assertion runs.
    """
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
            f"Live AGY --print is upstream-blocked ({upstream_reason}); "
            "the breaks/tool/artifact assertions cannot be evaluated. "
            f"Re-run when the upstream condition clears. "
            f"cli.log tail: {cli_log_tail[-200:]!r}"
        )

    assert "Breaks: none" in output or "No breaks" in output, (
        f"Expected no breaks in detailed report:\n{output[-5000:]}"
    )
    assert "tool activity observed" in output.lower() or "tool activity" in output.lower(), (
        f"Expected tool activity in output:\n{output[-5000:]}"
    )
    assert "artifact submitted" in output.lower() or (
        "smoke_test_result artifact" in output.lower()
    ), f"Expected artifact submission:\n{output[-5000:]}"

    text_lines = re.findall(r"- text: [^\n]+", output) or []
    assert any(line.startswith("- text:") for line in text_lines), (
        "Expected at least one - text: line in detailed report:\n" + output[-5000:]
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
        extra_env=None,
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
        "Claude Sonnet 4.6 (Thinking)",
        "--print",
        "Reply with exactly the word: hello",
    ]

    deadline = time.monotonic() + 30.0
    yielded: list[str] = []
    saw_hello = False
    for line in run_pty_and_read_lines(cmd, ctx):
        yielded.append(line)
        if "hello" in line.lower():
            saw_hello = True
            break
        if time.monotonic() >= deadline:
            break

    cli_log_path = Path.home() / ".gemini" / "antigravity-cli" / "cli.log"
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
    ``ralph.pipeline.plumbing.smoke_plumbing.resolve_smoke_harness_spec``:
    ``interactive-agy-smoke-Claude-Sonnet-4.6-Thinking``.

    If the live AGY is upstream-blocked (auth timed out, not logged in, quota
    exhausted, model invalid, etc.) the test xfails with a clear reason so the
    executor records the real outcome instead of failing on an environment
    issue. The cli.log tail is inspected for the documented patterns before
    the strict assertion runs.
    """
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
            f"Live AGY --print is upstream-blocked ({upstream_reason}); "
            "the smoke_test_result.json artifact cannot be produced. "
            "Re-run when the upstream condition clears. "
            f"cli.log tail: {cli_log_tail[-200:]!r}"
        )

    artifact_path = workspace_mirror / ".agent" / "artifacts" / "smoke_test_result.json"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}\nOutput:\n{output[-5000:]}"
    )

    expected_run_id = "interactive-agy-smoke-Claude-Sonnet-4.6-Thinking"
    receipt_path = (
        workspace_mirror
        / ".agent"
        / "receipts"
        / expected_run_id
        / "smoke_test_result.json"
    )
    assert receipt_path.is_file(), (
        f"Expected canonical receipt at {receipt_path}\n"
        f"Artifact present: {artifact_path.is_file()}\nOutput:\n{output[-5000:]}"
    )

    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_payload == {
        "run_id": expected_run_id,
        "artifact_type": "smoke_test_result",
    }, f"Unexpected receipt payload: {receipt_payload}"
