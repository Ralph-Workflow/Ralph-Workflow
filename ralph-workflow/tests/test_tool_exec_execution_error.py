"""Tests for ExecutionError message rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import pytest

from ralph import timeout_defaults as td
from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.coordination import ToolContent
from ralph.mcp.tools.exec import (
    PROCESS_EXEC_BOUNDED_CAPABILITY,
    _CompletedProcessAdapter,
    handle_exec_command,
)
from ralph.process.manager import DeadlineTimeoutExpired
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

TimeoutCause = Literal[
    "inactivity",
    "hard_cap",
    "stalled",
    "no_progress",
    "abandoned",
    "session_end",
    "restart_lost",
]

#: Every cause the exec family can report. ``None`` is the legacy
#: "cause unknown" path and must render both interpretations.
_ALL_CAUSES: tuple[TimeoutCause | None, ...] = (
    "inactivity",
    "hard_cap",
    "stalled",
    "no_progress",
    "abandoned",
    "session_end",
    "restart_lost",
    None,
)

#: A serialized liveness verdict, shaped exactly as
#: ``serialize_liveness_verdict`` emits it.
_VERDICT: dict[str, object] = {
    "score": 0.1,
    "pressure": 92.0,
    "stall_window_s": 90.0,
    "strongest_rule": "R6",
    "suspicious": True,
    "verdict_kind": "stuck",
    "signals": {"output_recent": 0.0, "novel_output": 0.0},
    "elapsed_s": 305.5,
}

#: Capabilities this server does not expose. The exec family has no
#: background / submit-and-poll mode: an exec call that outlives the
#: soft-reply budget already returns a ``job_id`` that ``exec_wait`` polls.
_NON_EXISTENT_ACTIONS: tuple[str, ...] = (
    "run it in the background",
    "run_in_background",
    "background execution is supported",
    "nohup",
    "tmux",
    "screen",
)


def _diagnostics(**overrides: object) -> str:
    payload = dict(_VERDICT)
    payload.update(overrides)
    return json.dumps(payload)


def _timeout_error(cause: TimeoutCause | None, *, populated: bool) -> ExecutionError:
    """Build a timed-out error, optionally with every data field populated."""
    if not populated:
        return ExecutionError(timed_out=True, timeout_cause=cause)
    return ExecutionError(
        timed_out=True,
        timeout_cause=cause,
        timeout_ms=90_000,
        elapsed_ms=305_500,
        ceiling_ms=3_600_000,
        suggested_timeout_ms=180_000,
        partial_output="hello\n",
        diagnostics=_diagnostics(),
    )


# ---------------------------------------------------------------------------
# Cache-full / workspace-limit templates
# ---------------------------------------------------------------------------


def test_cache_full_message_describes_automatic_reset_without_internal_tool() -> None:
    err = ExecutionError(
        "exec cache exceeds hard cap even after automatic reset",
        current_bytes=500,
        cap_bytes=200,
        removed_paths=3,
        removed_bytes=0,
        remaining_bytes=500,
        diagnostics="current=500 cap=200 removed=3 active_slots=1 attributed=300",
    )
    message = str(err)
    assert "automatic" in message.lower() or "reset" in message.lower(), (
        "cache-full message must describe automatic reset attempt"
    )
    assert (
        "active" in message.lower() or "live" in message.lower() or "permission" in message.lower()
    ), "cache-full message must explain why bytes remain (active slots / permissions)"
    assert "unacquirable locks" not in message, (
        "cache-full message must not contain lock-era wording"
    )
    assert "Cleanup+reset" not in message, (
        "cache-full message must not use old Cleanup+reset wording"
    )
    assert "cooldown" not in message.lower(), "cache-full message must not contain cooldown wording"
    assert "active_leases" not in message, (
        "cache-full message must not contain old lock-era active_leases field"
    )


# ---------------------------------------------------------------------------
# Job kill causes (ExecJobRegistry) — cause-accurate, real data
# ---------------------------------------------------------------------------


def test_stalled_kill_never_claims_no_output() -> None:
    """``stalled`` is a liveness verdict, not an output-silence verdict.

    Spec rule R6 kills a job that is printing the same line in a loop, so
    "No output was received" is factually wrong on this path. The message must
    name stall pressure and the stall window instead.
    """
    err = ExecutionError(
        "Job job_x terminated: stalled",
        timed_out=True,
        timeout_cause="stalled",
        timeout_ms=90_000,
        elapsed_ms=305_500,
        diagnostics=_diagnostics(),
    )

    text = str(err)
    lowered = text.lower()
    assert "stall" in lowered, "the message must name the stall cause"
    assert "no output" not in lowered, (
        "a stalled job can be producing output; the message must not claim silence"
    )
    assert "90000" in text, "the stall window (timeout_ms) must be reported"
    assert "305500" in text, "the real elapsed time must be reported"


def test_stalled_kill_does_not_render_the_stall_window_as_elapsed_time() -> None:
    """``timeout_ms`` is the stall window under the job architecture, not the
    time the command ran. Rendering it as elapsed time is the false claim this
    unit exists to remove."""
    err = ExecutionError(
        timed_out=True,
        timeout_cause="stalled",
        timeout_ms=90_000,
        elapsed_ms=305_500,
    )

    text = str(err)
    assert "after 90000ms" not in text, (
        "the stall window must never be presented as the elapsed runtime"
    )
    assert "after 305500ms" in text, "the real elapsed runtime must be presented instead"


def test_stalled_kill_with_no_elapsed_data_omits_the_number() -> None:
    err = ExecutionError(timed_out=True, timeout_cause="stalled", timeout_ms=90_000)

    text = str(err)
    assert "after 90000ms" not in text
    assert "?" not in text
    assert "None" not in text
    assert "stall" in text.lower()


def test_job_kill_surfaces_the_verdict_payload_but_never_guesses_a_duration() -> None:
    """The serialized liveness verdict rides in ``diagnostics``. It is surfaced
    verbatim, but the renderer does not mine it for a duration — so a job kill
    with no explicit ``elapsed_ms`` carries no elapsed number."""
    err = ExecutionError(
        timed_out=True,
        timeout_cause="stalled",
        diagnostics=_diagnostics(),
    )

    text = str(err)
    assert _VERDICT["strongest_rule"] in text, "the verdict payload must stay visible to the agent"
    header = text.splitlines()[0]
    assert "after " not in header, f"no known elapsed time means no duration: {header!r}"


def test_job_hard_cap_names_the_job_ceiling_not_the_legacy_300s_cap() -> None:
    err = ExecutionError(
        "Job job_y terminated: hard_cap",
        timed_out=True,
        timeout_cause="hard_cap",
        timeout_ms=90_000,
        elapsed_ms=3_600_000,
        ceiling_ms=3_600_000,
    )

    text = str(err)
    assert "ceiling" in text.lower(), "the job hard cap is a ceiling"
    assert "3600000" in text, "the effective job ceiling must be reported"
    assert "EXEC_MAX_TIMEOUT_MS" not in text, (
        "the legacy 300s constant does not bound jobs and must not be named"
    )
    assert str(EXEC_MAX_TIMEOUT_MS) not in text
    assert "max_runtime_ms" in text, "the per-call lever for the job ceiling must be named"


def test_job_hard_cap_without_ceiling_data_still_names_the_ceiling() -> None:
    err = ExecutionError(timed_out=True, timeout_cause="hard_cap")

    text = str(err)
    assert "ceiling" in text.lower()
    assert "?" not in text
    assert "None" not in text
    assert str(EXEC_MAX_TIMEOUT_MS) not in text, (
        "with no ceiling data the message must not invent the legacy 300s cap"
    )


def test_no_progress_kill_names_the_real_progress_window() -> None:
    err = ExecutionError(
        timed_out=True,
        timeout_cause="no_progress",
        elapsed_ms=920_000,
        diagnostics=_diagnostics(elapsed_s=920.0),
    )

    text = str(err)
    lowered = text.lower()
    assert "progress" in lowered
    assert "no output" not in lowered, "no_progress is a liveness verdict, not a silence verdict"
    assert f"{int(td.EXEC_NO_PROGRESS_WINDOW_S)}s" in text, (
        "the real no-progress window must be reported, not a stale constant name"
    )
    assert "EXEC_NO_PROGRESS_WINDOW_S" not in text, (
        "naming the constant without its value is not actionable"
    )


def test_abandoned_kill_names_the_abandonment_window_and_the_polling_action() -> None:
    err = ExecutionError(timed_out=True, timeout_cause="abandoned", elapsed_ms=300_000)

    text = str(err)
    lowered = text.lower()
    assert "abandon" in lowered
    assert f"{int(td.EXEC_JOB_ABANDON_S)}s" in text, "the real abandonment window must be reported"
    assert "exec_wait" in lowered, "the only way to avoid abandonment is polling with exec_wait"


def test_session_end_kill_says_the_session_ended() -> None:
    err = ExecutionError(timed_out=True, timeout_cause="session_end", elapsed_ms=120_000)

    text = str(err)
    assert "session" in text.lower()
    assert "?" not in text
    assert "None" not in text


def test_restart_lost_kill_says_the_job_did_not_survive_a_restart() -> None:
    err = ExecutionError(timed_out=True, timeout_cause="restart_lost", elapsed_ms=60_000)

    text = str(err)
    assert "restart" in text.lower()
    assert "?" not in text
    assert "None" not in text


# ---------------------------------------------------------------------------
# Legacy in-request paths — preserved, and provably distinct from job kills
# ---------------------------------------------------------------------------


def test_legacy_inactivity_still_names_the_output_silence_window() -> None:
    """A plain in-request exec really is killed by output silence, so the
    silence wording stays on this path only."""
    err = ExecutionError(
        timed_out=True,
        timeout_ms=90_000,
        timeout_cause="inactivity",
        suggested_timeout_ms=180_000,
    )

    lowered = str(err).lower()
    assert "no output" in lowered
    assert "resets" in lowered
    assert "180000" in lowered


def test_legacy_hard_cap_still_names_the_absolute_ceiling() -> None:
    """``run_command`` arms ``hard_timeout=EXEC_MAX_TIMEOUT_MS``; on THAT path
    the 300s ceiling genuinely fired, so naming it is correct there."""
    err = ExecutionError(
        timed_out=True,
        timeout_ms=EXEC_MAX_TIMEOUT_MS,
        deadline_timeout_ms=EXEC_MAX_TIMEOUT_MS,
        timeout_cause="hard_cap",
    )

    text = str(err)
    lowered = text.lower()
    assert "ceiling" in lowered
    assert f"{EXEC_MAX_TIMEOUT_MS}ms" in text
    assert "split" in lowered, "the ceiling cannot be raised, so splitting is the lever"
    assert "pass a larger timeout_ms" not in text


def test_legacy_and_job_paths_are_distinguished_by_their_data() -> None:
    """The two hard-cap paths must not render the same sentence: the legacy
    path really did hit a 300s absolute cap, the job path hit a job ceiling."""
    legacy = ExecutionError(
        timed_out=True,
        timeout_ms=EXEC_MAX_TIMEOUT_MS,
        deadline_timeout_ms=EXEC_MAX_TIMEOUT_MS,
        timeout_cause="hard_cap",
    )
    job = ExecutionError(
        timed_out=True,
        timeout_cause="hard_cap",
        elapsed_ms=3_600_000,
        ceiling_ms=3_600_000,
    )

    assert str(legacy) != str(job)
    assert f"{EXEC_MAX_TIMEOUT_MS}ms" in str(legacy)
    assert f"{EXEC_MAX_TIMEOUT_MS}ms" not in str(job)


@pytest.mark.parametrize("cause", _ALL_CAUSES)
@pytest.mark.parametrize("populated", [False, True])
def test_no_cause_suggests_a_capability_that_does_not_exist(
    cause: TimeoutCause | None,
    populated: bool,
) -> None:
    lowered = str(_timeout_error(cause, populated=populated)).lower()
    for banned in _NON_EXISTENT_ACTIONS:
        assert banned not in lowered, f"cause={cause!r} must not suggest {banned!r}"


@pytest.mark.parametrize("cause", _ALL_CAUSES)
@pytest.mark.parametrize("populated", [False, True])
def test_no_cause_renders_a_placeholder_or_empty_substitution(
    cause: TimeoutCause | None,
    populated: bool,
) -> None:
    text = str(_timeout_error(cause, populated=populated))

    assert text.strip(), f"cause={cause!r} must render a non-empty message"
    assert "?" not in text, f"cause={cause!r} rendered a placeholder '?': {text!r}"
    assert "None" not in text, f"cause={cause!r} rendered a Python None: {text!r}"
    assert "{}" not in text
    assert re.search(r"\(\s*\)", text) is None, f"empty parenthetical rendered: {text!r}"
    assert all(line == line.rstrip() for line in text.splitlines()), (
        f"cause={cause!r} rendered a line with a trailing empty substitution: {text!r}"
    )


def test_malformed_diagnostics_do_not_break_rendering() -> None:
    err = ExecutionError(timed_out=True, timeout_cause="stalled", diagnostics="not-json-at-all")

    text = str(err)
    assert "?" not in text
    assert "stall" in text.lower()


def test_unknown_cause_presents_both_interpretations() -> None:
    """A cause-less timeout is ambiguous: the command may be legitimately long
    (raise the limit) OR genuinely stuck (raising the limit only wastes more
    time). The message must surface BOTH so the agent does not reflexively
    double the timeout on a wedged command."""
    err = ExecutionError(
        "Failed to execute 'x': timed out after 90000ms",
        timed_out=True,
        timeout_ms=90_000,
        suggested_timeout_ms=180_000,
    )
    message = str(err).lower()
    assert "timeout_ms" in message
    assert "180000" in message
    assert any(word in message for word in ("loop", "stuck", "hang", "deadlock"))


def test_timeout_message_without_suggestion_still_warns_about_stuck_commands() -> None:
    err = ExecutionError("Failed to execute 'x': timed out", timed_out=True, timeout_ms=0)
    message = str(err).lower()
    assert any(word in message for word in ("loop", "stuck", "hang", "deadlock"))


@pytest.mark.parametrize(
    ("timeout_ms", "timeout_cause", "expected_ms", "expected_suggestion"),
    [
        (EXEC_MAX_TIMEOUT_MS, "hard_cap", EXEC_MAX_TIMEOUT_MS, None),
        (5_000, "inactivity", 5_000, 10_000),
    ],
)
def test_run_command_timeout_reports_the_expired_deadline_and_useful_suggestion(
    tmp_path: Path,
    timeout_ms: int,
    timeout_cause: Literal["hard_cap", "inactivity"],
    expected_ms: int,
    expected_suggestion: int | None,
) -> None:
    """End-to-end through the real handler: the rendered message must carry the
    window that actually expired and a suggestion strictly below the cap."""

    def timeout_runner(
        _argv: list[str], _cwd: Path, timeout: float | None
    ) -> _CompletedProcessAdapter:
        raise DeadlineTimeoutExpired(["slow"], timeout or 1, timeout_cause=timeout_cause)

    result = handle_exec_command(
        MockSession({PROCESS_EXEC_BOUNDED_CAPABILITY}),
        MockWorkspaceRoot(tmp_path),
        {"command": "slow", "timeout_ms": timeout_ms},
        deps=ExecRunDeps(runner=timeout_runner),
    )

    content = result.content[0]
    assert result.is_error is True
    assert isinstance(content, ToolContent)
    assert f"{expected_ms}ms" in content.text
    if expected_suggestion is None:
        assert "Suggested timeout_ms" not in content.text
    else:
        assert f"Suggested timeout_ms: {expected_suggestion}." in content.text
