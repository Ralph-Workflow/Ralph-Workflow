from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    AgentRunCtx,
    AttemptEnded,
    attempt_facts_for_failure,
    run_subprocess_and_read_lines,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.recovery.attempt_facts import TerminationIssuer, has_return_code_discrepancy

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]

_FAKE_CLI = Path(__file__).with_name("fake_cli")
_KILL_RETURN_CODES = frozenset({-15, 143})


def _ctx(
    workspace: Path,
    *,
    descendant_timeout: float = 0.1,
    requires_completion_evidence: bool = False,
) -> AgentRunCtx:
    return AgentRunCtx(
        config=AgentConfig(cmd=sys.executable, transport=AgentTransport.GENERIC),
        show_progress=False,
        extra_env=None,
        workspace_path=workspace,
        policy=TimeoutPolicy(
            idle_timeout_seconds=2.0,
            max_session_seconds=3.0,
            parent_exit_grace_seconds=0.02,
            descendant_wait_timeout_seconds=descendant_timeout,
            descendant_wait_poll_seconds=0.01,
        ),
        requires_completion_evidence=requires_completion_evidence,
        execution_strategy=(OpenCodeExecutionStrategy() if requires_completion_evidence else None),
    )


def _command(script: str) -> list[str]:
    return [sys.executable, str(_FAKE_CLI / script)]


def _drain(stream: Generator[str, None, AttemptEnded]) -> tuple[list[str], AttemptEnded]:
    lines: list[str] = []
    while True:
        try:
            lines.append(next(stream))
        except StopIteration as stopped:
            carried: object = stopped.value
            assert isinstance(carried, AttemptEnded)
            return lines, carried


def test_tool_result_quoting_credits_exhausted_does_not_kill_session(tmp_path: Path) -> None:
    stream = run_subprocess_and_read_lines(_command("credits_in_tool_result.py"), _ctx(tmp_path))
    try:
        lines, attempt_ended = _drain(stream)
    except AgentInvocationError as exc:
        pytest.fail(f"the healthy session was killed: {exc}")

    assert any('"error": null' in line and "credits exhausted" in line for line in lines)

    termination = attempt_ended.facts.termination
    assert termination is not None
    assert termination.issuer is not TerminationIssuer.QUOTA_DETECTOR, (
        "a tool result quoting the quota wording must never be attributed to the quota detector"
    )
    assert termination.issuer is TerminationIssuer.AGENT_EXIT, (
        "a healthy session that ends on its own is attributed to the agent exit"
    )
    assert termination.observed_returncode == 0
    assert termination.reported_returncode == 0
    assert has_return_code_discrepancy(termination) is False


def test_sigterm_during_subagent_activity_records_return_codes_and_issuer(tmp_path: Path) -> None:
    with pytest.raises(AgentInvocationError) as caught:
        list(run_subprocess_and_read_lines(_command("subagent_sigterm.py"), _ctx(tmp_path)))

    facts = attempt_facts_for_failure(caught.value)
    termination = facts.termination
    assert termination is not None
    assert termination.issuer is TerminationIssuer.UNKNOWN
    assert termination.observed_returncode in _KILL_RETURN_CODES
    assert termination.reported_returncode in _KILL_RETURN_CODES, (
        "the kill signal is the code reported upward; a zero reported next to a kill"
        " hides the kill from the attempt facts"
    )
    assert has_return_code_discrepancy(termination) is (
        termination.observed_returncode != termination.reported_returncode
    )


def test_zero_exit_with_non_quiescing_descendant_attributes_post_exit_watchdog(
    tmp_path: Path,
) -> None:
    with pytest.raises(AgentInvocationError) as caught:
        list(
            run_subprocess_and_read_lines(
                _command("descendant_hang.py"),
                _ctx(tmp_path, requires_completion_evidence=True),
            )
        )

    facts = attempt_facts_for_failure(caught.value)
    termination = facts.termination
    assert termination is not None
    assert termination.issuer is TerminationIssuer.POST_EXIT_WATCHDOG
    assert termination.observed_returncode == 0
    assert termination.reported_returncode == 0
    assert has_return_code_discrepancy(termination) is False
