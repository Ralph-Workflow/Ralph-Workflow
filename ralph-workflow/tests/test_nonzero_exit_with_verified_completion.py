from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.config.enums import AgentTransport
from tests.fake_handle import _FakeHandle

_RUN_ID = "run-1"


def _options(workspace: Path, transport: AgentTransport) -> CompletionCheckOptions:
    return CompletionCheckOptions(
        execution_strategy=strategy_for_transport(transport),
        workspace_path=workspace,
        policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
        completion_run_id=_RUN_ID,
    )


def _write_sentinel(workspace: Path, run_id: str) -> None:
    sentinel = workspace / ".agent" / f"completion_seen_{run_id}.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")


@pytest.mark.parametrize("transport", [AgentTransport.CODEX, AgentTransport.CLAUDE])
def test_ralph_issued_kill_after_completion_is_success(
    tmp_path: Path, transport: AgentTransport
) -> None:
    _write_sentinel(tmp_path, _RUN_ID)

    check_process_result(
        _FakeHandle(returncode=143, termination_issuer="ralph"),
        "agent",
        [],
        _options(tmp_path, transport),
    )


@pytest.mark.parametrize("returncode", [1, 2, 137])
def test_crash_after_completion_is_success(tmp_path: Path, returncode: int) -> None:
    _write_sentinel(tmp_path, _RUN_ID)

    check_process_result(
        _FakeHandle(returncode=returncode), "agent", [], _options(tmp_path, AgentTransport.CODEX)
    )


def test_kill_without_completion_evidence_still_fails(tmp_path: Path) -> None:
    with pytest.raises(AgentInvocationError) as raised:
        check_process_result(
            _FakeHandle(returncode=143, termination_issuer="ralph"),
            "agent",
            [],
            _options(tmp_path, AgentTransport.CODEX),
        )

    assert raised.value.returncode == 143


def test_completion_evidence_from_another_run_does_not_count(tmp_path: Path) -> None:
    _write_sentinel(tmp_path, "some-other-run")

    with pytest.raises(AgentInvocationError):
        check_process_result(
            _FakeHandle(returncode=1), "agent", [], _options(tmp_path, AgentTransport.CODEX)
        )


def test_echoed_completion_marker_in_output_does_not_count(tmp_path: Path) -> None:
    echoed = ["[Completion event emitted to pipeline]", "declare_complete"]

    with pytest.raises(AgentInvocationError):
        check_process_result(
            _FakeHandle(returncode=1), "agent", echoed, _options(tmp_path, AgentTransport.CODEX)
        )
