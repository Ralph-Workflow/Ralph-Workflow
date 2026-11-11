from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, BaseExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    check_process_result,
)
from ralph.agents.invoke._open_code_resumable_exit_error import (
    OpenCodeResumableExitError,
)
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from ralph.agents.execution_state import LiveDescendantHandle
    from ralph.process.liveness import LivenessProbe


class _ProducerStubStrategy(BaseExecutionStrategy):
    def __init__(self) -> None:
        super().__init__()
        self.classify_exit_calls = 0

    def supports_session_continuation(self) -> bool:
        return True

    def supports_completion_enforcement(self) -> bool:
        return True

    def classify_exit(
        self,
        handle: LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, completion_signals, liveness_probe
        self.classify_exit_calls += 1
        return AgentExecutionState.RESUMABLE_CONTINUE


class _ProducerStubHandle:
    returncode = 0
    pid = None


def _stub_eval(
    workspace_path: object,
    bounded_output: list[str],
    **kwargs: object,
) -> CompletionSignals:
    del workspace_path, bounded_output, kwargs
    return CompletionSignals(
        explicit_complete=False,
        required_artifact_present=False,
        artifact_types=(),
    )


def _stub_sentinel(workspace_path: object, run_id: object) -> bool:
    del workspace_path, run_id
    return False


def _make_producer_options(
    strategy: BaseExecutionStrategy, *, captured_session_id: str
) -> CompletionCheckOptions:
    policy = TimeoutPolicy(
        idle_timeout_seconds=None,
        parent_exit_grace_seconds=0.0,
        descendant_wait_timeout_seconds=0.0,
        descendant_wait_poll_seconds=0.001,
    )
    return CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=Path("synthetic://producer-path"),
        liveness_probe=None,
        policy=policy,
        required_artifact=None,
        explicit_completion_seen=False,
        captured_session_id=captured_session_id,
        completion_run_id=None,
        evaluate_completion_fn=_stub_eval,
        _sentinel_check_fn=_stub_sentinel,
    )


def test_producer_path_raises_typed_exception_when_strategy_classifies_resumable_continue() -> None:
    strategy = _ProducerStubStrategy()
    handle = _ProducerStubHandle()
    opts = _make_producer_options(strategy, captured_session_id="sess-producer-test")

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output=[],
            check_options=opts,
            _clock=FakeClock(),
        )

    assert excinfo.value.resumable_session_id == "sess-producer-test"
    assert strategy.classify_exit_calls >= 1
    assert excinfo.value.agent_name == "opencode"


def test_producer_path_prefers_captured_session_id_over_extractor() -> None:
    strategy = _ProducerStubStrategy()
    handle = _ProducerStubHandle()
    opts = _make_producer_options(strategy, captured_session_id="sess-captured-wins")

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output=["Session ID: sess-from-bounded-output"],
            check_options=opts,
            _clock=FakeClock(),
        )

    assert excinfo.value.resumable_session_id == "sess-captured-wins"


__all__ = [
    "_ProducerStubHandle",
    "_ProducerStubStrategy",
    "_make_producer_options",
    "test_producer_path_prefers_captured_session_id_over_extractor",
    "test_producer_path_raises_typed_exception_when_strategy_classifies_resumable_continue",
]
