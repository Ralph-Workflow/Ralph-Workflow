from __future__ import annotations

import contextlib
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
from ralph.agents.invoke import AgentInvocationError, run_pty_and_read_lines
from ralph.agents.invoke._agent_run_ctx import AgentRunCtx
from ralph.agents.timeout_clock import SystemClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.display.line_sanitizer import strip_terminal_control
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.pipeline.plumbing.smoke_plumbing import resolve_smoke_harness_spec

_REAL_HOME = Path(os.environ.get("HOME") or Path.home()).resolve()

_LIVE_AGY_AGENT = "agy/gemini-3.6-flash-low"
_LIVE_AGY_EXPECTED_RUN_ID = resolve_smoke_harness_spec(_LIVE_AGY_AGENT).run_id


def _observed_parser_classified_lines(output: str) -> list[str]:
    observed = strip_terminal_control(output).split("Observed output:", 1)[-1]
    observed_output, _, _ = observed.partition("Observed breaks:")
    return [
        line
        for raw_line in observed_output.splitlines()
        if (line := raw_line.strip().lstrip("│ ").strip())
        and re.fullmatch(r"- (?:text|thinking|tool_use|tool_result|error): \S.*", line)
    ]


def _quick_policy() -> object:
    return TimeoutPolicy(
        idle_timeout_seconds=45.0,
        process_exit_wait_seconds=15.0,
    )


pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.live_agy,
    pytest.mark.smoke,
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


def _get_live_agy_sandbox_home() -> Path:
    sandbox = Path(tempfile.gettempdir()) / "ralph-live-agy-sandbox-home"
    sandbox.mkdir(parents=True, exist_ok=True)
    real_gemini = _REAL_HOME / ".gemini"
    sandbox_gemini = sandbox / ".gemini"
    if real_gemini.exists() and not sandbox_gemini.exists():
        with contextlib.suppress(OSError):
            shutil.copytree(real_gemini, sandbox_gemini, symlinks=True)
    return sandbox


def _build_live_env() -> dict[str, str]:
    sandbox_home = _get_live_agy_sandbox_home()
    env = {
        **os.environ,
        "HOME": str(sandbox_home),
        "XDG_CONFIG_HOME": str(sandbox_home / ".config"),
    }
    env.pop("PYTHONPATH", None)
    env.pop("RALPH_AGY_BINARY", None)
    return env


def _read_cli_log_tail(home: Path) -> str:
    cli_log_path = home / ".gemini" / "antigravity-cli" / "cli.log"
    if not cli_log_path.is_file():
        return ""
    try:
        return cli_log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
    except OSError:
        return "<unreadable>"


@dataclass(frozen=True)
class _LiveSmokeResult:
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
    return _build_live_env()


@pytest.fixture(scope="session")
def live_smoke_session() -> Generator[_LiveSmokeResult, None, None]:
    workspace = Path(tempfile.mkdtemp(prefix="agy-live-smoke-"))
    prompt_file = workspace / "tmp" / "interactive-agy-smoke" / "PROMPT.md"
    _write_smoke_prompt(prompt_file)

    env = _build_live_env()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ralph", "smoke-interactive-agy", "--agent", _LIVE_AGY_AGENT],
            capture_output=True,
            text=True,
            cwd=workspace,
            env=env,
            timeout=240,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cli_log_tail = _read_cli_log_tail(_REAL_HOME)
        timeout_diagnostic = _live_timeout_diagnostic(exc.timeout, cli_log_tail)
        if timeout_diagnostic is not None:
            pytest.xfail(timeout_diagnostic)
        raise
    output = result.stdout + result.stderr
    cli_log_tail = _read_cli_log_tail(_REAL_HOME)

    yield _LiveSmokeResult(
        output=output,
        cli_log_tail=cli_log_tail,
        workspace=workspace,
        expected_run_id=_LIVE_AGY_EXPECTED_RUN_ID,
    )


def _xfail_if_upstream_blocked(cli_log_tail: str, *, smoke_output: str = "") -> None:
    upstream_reason = _detect_upstream_blocked_reason(cli_log_tail)
    if upstream_reason is not None:
        pytest.xfail(
            f"Live AGY is upstream-blocked ({upstream_reason}); "
            "the strict live assertions cannot be observed in this env. "
            "The mock-binary test test_agy_smoke_promotes_artifact_to_canonical_receipt "
            "is the always-green regression-proof. cli.log tail: "
            f"{cli_log_tail[-200:]!r}"
        )
    if smoke_output and "[claude turn boundary]" in smoke_output:
        pytest.xfail(
            "Sandbox artifact ([claude turn boundary]) corrupted the raw "
            "transcript; the strict 'No breaks observed' assertion is "
            "unobservable in this env. See agy_wire_provenance.md "
            "'Unexplained, out of scope' for the documented environment-level "
            "nature of this artifact. cli.log tail: "
            f"{cli_log_tail[-200:]!r}"
        )


def test_live_agy_invokes_live_binary(live_smoke_session: _LiveSmokeResult) -> None:
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    output = live_smoke_session.output

    assert any(
        "Invoking agent: agy" in line and "--dangerously-skip-permissions" in line
        for line in output.splitlines()
    ), f"Expected live invocation line in output:\n{output[-5000:]}"
    assert "MOCK_AGY_BEHAVIOR=" not in output, (
        f"Mock marker should not appear in live run:\n{output[-5000:]}"
    )


def test_live_agy_produces_green_parity_table(
    live_smoke_session: _LiveSmokeResult,
) -> None:
    output = live_smoke_session.output
    cli_log_tail = live_smoke_session.cli_log_tail
    _xfail_if_upstream_blocked(cli_log_tail, smoke_output=output)

    assert "agy/gemini-3.6-flash-low" in output, (
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
    required_facts_wire = (
        "- smoke_test_result artifact submitted observed [WIRE]" in output
        and "- completion sentinel observed [WIRE]" in output
        and "- tool activity observed [WIRE]" in output
    )
    if required_facts_wire:
        assert re.search(r"│\s*none\s*│", output) is not None, (
            f"Expected Breaks=none in parity table (all required facts graded WIRE). "
            f"cli.log tail: {cli_log_tail[-200:]!r}\n"
            f"Output:\n{output[-5000:]}"
        )
    else:
        assert "DEGR" in output, (
            f"Expected a DEGRADED verdict column (at least one required fact graded "
            f"below WIRE). cli.log tail: {cli_log_tail[-200:]!r}\n"
            f"Output:\n{output[-5000:]}"
        )


def test_live_agy_artifact_present(live_smoke_session: _LiveSmokeResult) -> None:
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    cli_log_tail = live_smoke_session.cli_log_tail
    output = live_smoke_session.output

    artifact_path = live_smoke_session.workspace / ".agent" / "artifacts" / "smoke_test_result.md"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )


def test_live_agy_no_breaks_and_tool_artifact_activity(
    live_smoke_session: _LiveSmokeResult,
) -> None:
    output = live_smoke_session.output
    cli_log_tail = live_smoke_session.cli_log_tail
    _xfail_if_upstream_blocked(cli_log_tail, smoke_output=output)

    assert "- tool activity observed" in output, (
        f"Expected the dash-prefixed '- tool activity observed' success marker. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert "- smoke_test_result artifact submitted" in output, (
        f"Expected the dash-prefixed '- smoke_test_result artifact submitted' "
        f"success marker. cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert "- completion sentinel observed" in output, (
        f"Expected the durable completion-sentinel success marker. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert any(
        f"- completion sentinel observed [{rung}]" in output
        for rung in ("TRANSCRIPT", "HOST_SYNTHESIZED", "WIRE")
    ), (
        "Expected the completion sentinel marker to name its provenance rung "
        "(TRANSCRIPT/HOST_SYNTHESIZED/WIRE), not a bare unqualified claim. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    required_facts_wire = (
        "- smoke_test_result artifact submitted observed [WIRE]" in output
        and "- completion sentinel observed [WIRE]" in output
        and "- tool activity observed [WIRE]" in output
    )
    if required_facts_wire:
        assert "No breaks observed" in output or "Breaks: none" in output, (
            f"Expected no breaks marker in parity report (all required facts graded WIRE). "
            f"cli.log tail: {cli_log_tail[-200:]!r}\n"
            f"Output:\n{output[-5000:]}"
        )
    else:
        assert "DEGRADED (" in output, (
            f"Expected a named DEGRADED demotion in parity report "
            f"(at least one required fact graded below WIRE). "
            f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
        )

    forbidden_negative_markers = (
        "- no tool activity was observed",
        "- smoke_test_result artifact was not submitted",
        "- expected todo-list.js was not created",
        "- completion sentinel was not observed",
        "- no parser events were observed",
        "- fewer than 3 meaningful output lines were observed",
    )
    present_negatives = [m for m in forbidden_negative_markers if m in output]
    assert not present_negatives, (
        f"Expected the parity report to contain no break markers, found: "
        f"{present_negatives}. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )

    parser_classified_lines = _observed_parser_classified_lines(output)
    assert parser_classified_lines, (
        "Expected non-empty parser-classified output under Observed output:; "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )


def test_live_agy_pty_read_thread_sees_output(
    workspace_mirror: Path,
) -> None:
    _xfail_if_live_agy_upstream_blocked()

    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    ctx = AgentRunCtx(
        config=config,
        show_progress=False,
        extra_env={
            "HOME": str(_get_live_agy_sandbox_home()),
            "XDG_CONFIG_HOME": str(_get_live_agy_sandbox_home() / ".config"),
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
        "gemini-3.6-flash-low",
        "--print",
        "Reply with exactly the word: hello",
    ]

    deadline = time.monotonic() + 120.0
    yielded: list[str] = []
    saw_hello = False
    try:
        for line in run_pty_and_read_lines(cmd, ctx):
            yielded.append(line)
            if "hello" in line.lower():
                saw_hello = True
                break
            if time.monotonic() >= deadline:
                break
    except AgentInvocationError:
        cli_log_tail = _read_cli_log_tail(_REAL_HOME)
        upstream_reason = _detect_upstream_blocked_reason(cli_log_tail)
        if upstream_reason is not None:
            pytest.xfail(
                f"Live AGY --print is upstream-blocked ({upstream_reason}); "
                "the canonical 'hello' response cannot be observed. "
                f"cli.log tail: {cli_log_tail[-200:]!r}"
            )
        raise

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


def _live_timeout_diagnostic(timeout_seconds: float, cli_log_tail: str) -> str | None:

    upstream_reason = _detect_upstream_blocked_reason(cli_log_tail)
    if upstream_reason is None:
        return None
    return (
        f"Live AGY exceeded the {timeout_seconds:g}s observation bound while upstream "
        f"authentication was unavailable: {upstream_reason}"
    )


def test_detect_upstream_blocked_reason_ignores_transient_not_logged_in_noise() -> None:

    cli_log_tail = (
        "You are not logged into Antigravity. Print mode: triggering interactive OAuth\n"
        "ChainedAuth succeeded"
    )

    assert _detect_upstream_blocked_reason(cli_log_tail) is None


def test_live_agy_timeout_regression_is_upstream_blocked_when_cli_log_records_auth() -> None:

    diagnostic = _live_timeout_diagnostic(
        120,
        "Print mode: auth timed out",
    )

    assert diagnostic == (
        "Live AGY exceeded the 120s observation bound while upstream authentication "
        "was unavailable: AGY --print auth timed out (no OAuth credentials in test env)"
    )


def _detect_upstream_blocked_reason(cli_log_tail: str) -> str | None:
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
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    cli_log_tail = live_smoke_session.cli_log_tail
    output = live_smoke_session.output

    artifact_path = live_smoke_session.workspace / ".agent" / "artifacts" / "smoke_test_result.md"
    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )

    expected_run_id = live_smoke_session.expected_run_id
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
def test_live_agy_produces_parser_classified_output_and_canonical_receipt(
    live_smoke_session: _LiveSmokeResult,
) -> None:
    _xfail_if_upstream_blocked(live_smoke_session.cli_log_tail)
    cli_log_tail = live_smoke_session.cli_log_tail
    output = live_smoke_session.output

    expected_run_id = live_smoke_session.expected_run_id
    artifact_path = live_smoke_session.workspace / ".agent" / "artifacts" / "smoke_test_result.md"

    assert artifact_path.is_file(), (
        f"Expected smoke_test_result artifact at {artifact_path}. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
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

    assert _observed_parser_classified_lines(output), (
        "Expected non-empty parser-classified output under Observed output: "
        "(bounded emission-order prefix may be tool-only). cli.log tail: "
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
    assert "- completion sentinel observed" in output, (
        "Expected the independent durable completion evidence in the report. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )
    assert any(
        f"- completion sentinel observed [{rung}]" in output
        for rung in ("TRANSCRIPT", "HOST_SYNTHESIZED", "WIRE")
    ), (
        "Expected the completion sentinel marker to name its provenance rung. "
        f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )


def test_live_agy_wire_dispatch_forces_pass_with_broker_secret() -> None:
    _xfail_if_live_agy_upstream_blocked()

    workspace = Path(tempfile.mkdtemp(prefix="agy-live-smoke-wire-"))
    prompt_file = workspace / "tmp" / "interactive-agy-smoke" / "PROMPT.md"
    _write_smoke_prompt(prompt_file)

    env = _build_live_env()
    env["RALPH_BROKER_SECRET"] = "test-live-wire-dispatch-secret"

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

    _xfail_if_upstream_blocked(cli_log_tail, smoke_output=output)

    assert "- smoke_test_result artifact submitted observed [WIRE]" in output, (
        "Expected the artifact-submission fact to reach WIRE provenance with "
        f"RALPH_BROKER_SECRET forced. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )
    assert "- completion sentinel observed [WIRE]" in output, (
        "Expected the completion-sentinel fact to reach WIRE provenance with "
        f"RALPH_BROKER_SECRET forced. cli.log tail: {cli_log_tail[-200:]!r}\n"
        f"Output:\n{output[-5000:]}"
    )
    assert "No breaks observed" in output or "Breaks: none" in output, (
        f"Expected a clean PASS with the secret forced. cli.log tail: "
        f"{cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
    )

    demoted_ranks = ("WORKSPACE_EFFECT", "HOST_SYNTHESIZED", "TRANSCRIPT", "ABSENT")
    for demoted_rank in demoted_ranks:
        assert f"- smoke_test_result artifact submitted observed [{demoted_rank}]" not in output, (
            f"Artifact submission graded {demoted_rank}, not WIRE, even with the "
            f"secret forced -- a real tools/call dispatch did not happen. "
            f"cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
        )
        assert f"- completion sentinel observed [{demoted_rank}]" not in output, (
            f"Completion sentinel graded {demoted_rank}, not WIRE, even with the "
            f"secret forced -- declare_complete was not dispatched via a real "
            f"tools/call. cli.log tail: {cli_log_tail[-200:]!r}\nOutput:\n{output[-5000:]}"
        )


def _xfail_if_live_agy_upstream_blocked() -> None:

    cli_log_tail = _read_cli_log_tail(_REAL_HOME)
    upstream_reason = _detect_upstream_blocked_reason(cli_log_tail)
    if upstream_reason is not None:
        pytest.xfail(
            "Live AGY is upstream-blocked before invocation "
            f"({upstream_reason}); cli.log tail: {cli_log_tail[-200:]!r}"
        )


def test_live_agy_environment_diagnostic_records_upstream_block(
    live_smoke_session: _LiveSmokeResult,
) -> None:
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
