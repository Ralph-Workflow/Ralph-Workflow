"""Black-box tests for the watchdog fire record in recovery.

A watchdog kill reaches the retry layer as ``AgentInactivityTimeoutError``,
which the retry text used to flatten to "an inactivity timeout ... The exact
cause may be unknown". Loop-breakers (``REPEATED_*``), the child ceiling and
the session ceiling all arrive through that same type, so an operator could
not tell a silent agent from one killed for looping, nor whether subagents
were live. The kill is now named in the retry prompt and appended to
``.agent/watchdog-fire-events.jsonl``.

No real subprocesses, no real wall clock, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import AgentInactivityTimeoutError, AgentInvocationError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.pipeline.effect_executor import AgentRecoveryInput, build_agent_recovery_plan
from ralph.pipeline.effects import InvokeAgentEffect

EVENTS_RELPATH = Path(".agent") / "watchdog-fire-events.jsonl"
IDLE_SECONDS = 300.0


def _prompt(tmp_path: Path) -> Path:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do the task", encoding="utf-8")
    return prompt


def _plan(tmp_path: Path, exc: Exception) -> str:
    plan = build_agent_recovery_plan(
        AgentRecoveryInput(
            exc=exc,
            attempt_index=0,
            max_recovery_attempts=3,
            effect=InvokeAgentEffect(
                agent_name="opencode",
                phase="development",
                prompt_file=str(_prompt(tmp_path)),
            ),
            workspace_root=tmp_path,
            raw_output=[],
            rendered_output=[],
            extracted_session_id=None,
            inactivity_error_type=AgentInactivityTimeoutError,
        )
    )
    assert plan is not None
    return Path(plan.prompt_file).read_text(encoding="utf-8")


def _quiet_kill(snapshot: dict[str, object]) -> AgentInactivityTimeoutError:
    return AgentInactivityTimeoutError(
        "opencode",
        IDLE_SECONDS,
        [],
        InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
            diagnostic={"watchdog_snapshot": snapshot},
        ),
    )


def _events(tmp_path: Path) -> list[dict[str, object]]:
    lines = (tmp_path / EVENTS_RELPATH).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_retry_prompt_names_the_rule_that_fired_and_the_live_work(tmp_path: Path) -> None:
    exc = _quiet_kill(
        {
            "live_subagent_count": 3,
            "mcp_tool_call_count": 41,
            "idle_elapsed_seconds": 301.2,
            "last_activity_kind": "mcp_tool_call",
            "last_alive_by": None,
        }
    )

    text = _plan(tmp_path, exc)

    assert "no_output_deadline" in text
    assert "live_subagent_count=3" in text
    assert "mcp_tool_call_count=41" in text
    assert "last_activity_kind=mcp_tool_call" in text
    assert "The exact cause may be unknown" not in text


def test_loop_breaker_kill_is_not_reported_as_plain_silence(tmp_path: Path) -> None:
    exc = AgentInactivityTimeoutError(
        "opencode",
        IDLE_SECONDS,
        [],
        InactivityTimeoutOpts(
            reason=WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
            diagnostic={"tool_name": "exec", "tool_args_preview": "make verify"},
        ),
    )

    text = _plan(tmp_path, exc)

    assert "repeated_identical_tool_call" in text
    assert "make verify" in text


def test_every_watchdog_kill_is_appended_to_the_events_file(tmp_path: Path) -> None:
    _plan(tmp_path, _quiet_kill({"live_subagent_count": 0}))
    _plan(
        tmp_path,
        AgentInactivityTimeoutError(
            "opencode",
            IDLE_SECONDS,
            [],
            InactivityTimeoutOpts(reason=WatchdogFireReason.SESSION_CEILING_EXCEEDED),
        ),
    )

    events = _events(tmp_path)

    assert [event["rule"] for event in events] == [
        "no_output_deadline",
        "session_ceiling_exceeded",
    ]
    assert events[0]["agent"] == "opencode"
    assert events[0]["phase"] == "development"
    assert events[0]["timeout_seconds"] == IDLE_SECONDS
    assert events[0]["snapshot"] == {"live_subagent_count": 0}
    assert isinstance(events[0]["recorded_at"], float)


def test_a_kill_without_a_snapshot_still_names_its_rule(tmp_path: Path) -> None:
    exc = AgentInactivityTimeoutError("opencode", IDLE_SECONDS, [])

    text = _plan(tmp_path, exc)

    assert "Agent produced no output for 300s" in text
    assert _events(tmp_path)[0]["rule"] is None


def test_a_non_watchdog_failure_writes_no_event_and_keeps_its_wording(tmp_path: Path) -> None:
    exc = AgentInvocationError("opencode", 1, "fetch failed")

    text = _plan(tmp_path, exc)

    assert "The exact cause may be unknown" in text
    assert not (tmp_path / EVENTS_RELPATH).exists()
