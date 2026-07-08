"""Black-box tests for the R7 producer-path root-cause regression.

R7 (Trustworthy Idle Watchdog product spec):

    Ambiguous rc=0 exits are root-caused, deterministically classified,
    and handled.

The companion classifier-side test file
(``tests/recovery/test_opencode_resumable_exit_classification.py``) pins
the CONSUMER side of the contract: ``FailureClassifier._categorize_exc``
maps ``OpenCodeResumableExitError`` to ``FailureCategory.AGENT`` and
threads ``resumable_session_id`` through.

THIS file pins the PRODUCER side: when an agent subprocess exits cleanly
(rc=0) without a completion artifact AND without ``declare_complete``,
``ralph.agents.invoke._completion._check_process_result`` raises
``OpenCodeResumableExitError`` with the captured ``session_id``. The
producer path is the ROOT CAUSE of the typed exception -- without it
the classifier-side determinism has nothing to consume.

The producer pin is the headline R7 root-cause test. A regression on
``_completion.py:368`` (the ``raise OpenCodeResumableExitError(...)``
statement) would silently break the watchdog-driven resume contract
(R4) -- the recovery controller would no longer have a typed exception
to lift ``resumable_session_id`` from, so a clean rc=0-no-evidence
exit would fall back to the ambiguous-warning path.

These tests are pure black-box: no real subprocess, no real time, no
real filesystem. The producer is driven end-to-end through a stub
execution strategy, stub handle, stub completion-check options, and
``FakeClock``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, BaseExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._completion import (
    _check_process_result,
    _CompletionCheckOptions,
)
from ralph.agents.invoke._open_code_resumable_exit_error import (
    OpenCodeResumableExitError,
)
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from ralph.agents.execution_state._live_descendant_handle import (
        _LiveDescendantHandle,
    )
    from ralph.process.liveness import LivenessProbe


class _ProducerStubStrategy(BaseExecutionStrategy):
    """Stub execution strategy whose ``classify_exit`` always returns RESUMABLE_CONTINUE.

    Drives the producer path in ``_check_process_result`` end-to-end:
    when ``classify_exit`` returns ``AgentExecutionState.RESUMABLE_CONTINUE``,
    the completion gate raises ``OpenCodeResumableExitError`` with the
    captured ``session_id`` -- the canonical rc=0-no-evidence producer.
    """

    def __init__(self) -> None:
        super().__init__()
        self.classify_exit_calls = 0

    def supports_session_continuation(self) -> bool:
        return True

    def supports_completion_enforcement(self) -> bool:
        return True

    def classify_exit(
        self,
        handle: _LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, completion_signals, liveness_probe
        self.classify_exit_calls += 1
        return AgentExecutionState.RESUMABLE_CONTINUE


class _ProducerStubHandle:
    """Stub ``ManagedProcess`` whose ``returncode`` is 0 and ``pid`` is None.

    ``_teardown_subtree_if_pid_available`` short-circuits when ``pid`` is
    None so no real subprocess teardown is invoked.
    """

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
) -> _CompletionCheckOptions:
    """Construct a stub ``_CompletionCheckOptions`` with deterministic stub fakes.

    The workspace_path argument is a string (NOT a real Path on disk) so the
    test does not touch the filesystem. The evaluate_completion_fn and
    _sentinel_check_fn stubs return deterministic CompletionSignals +
    ``False`` respectively. The policy has ``parent_exit_grace_seconds=0.0``
    so the post-exit watchdog does not poll for a real-time grace window.
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=None,
        parent_exit_grace_seconds=0.0,
        descendant_wait_timeout_seconds=0.0,
        descendant_wait_poll_seconds=0.001,
    )
    return _CompletionCheckOptions(
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
    """R7 producer: ``_check_process_result`` raises ``OpenCodeResumableExitError``.

    Drives the producer path end-to-end: a stub strategy whose
    ``classify_exit`` returns ``AgentExecutionState.RESUMABLE_CONTINUE``
    + a stub handle with ``returncode=0`` + a stub ``_CompletionCheckOptions``
    with ``captured_session_id='sess-producer-test'`` triggers the
    ``raise OpenCodeResumableExitError(agent_name, session_id=session_id)``
    statement at ``ralph/agents/invoke/_completion.py:368`` -- the canonical
    rc=0-no-evidence producer.

    The captured ``session_id`` MUST propagate to the raised exception's
    ``resumable_session_id`` attribute so the failure classifier can lift
    it into ``ClassifiedFailure.resumable_session_id`` (R4 + R7 contract).
    A regression on the producer would silently drop the captured id and
    break the watchdog-driven resume flow.

    The test is pure black-box: no real subprocess, no real filesystem,
    no real wall-clock waits (``parent_exit_grace_seconds=0.0`` so the
    post-exit watchdog does not poll).
    """
    strategy = _ProducerStubStrategy()
    handle = _ProducerStubHandle()
    opts = _make_producer_options(strategy, captured_session_id="sess-producer-test")

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        _check_process_result(
            handle,
            "opencode",
            parsed_output=[],
            check_options=opts,
            _clock=FakeClock(),
        )

    # The producer raised the typed exception.
    assert excinfo.value.resumable_session_id == "sess-producer-test"
    # The strategy was consulted via classify_exit (the producer drove the
    # stub at least once).
    assert strategy.classify_exit_calls >= 1
    # The exception's agent_name matches what we passed in.
    assert excinfo.value.agent_name == "opencode"


def test_producer_path_prefers_captured_session_id_over_extractor() -> None:
    """R7 producer: ``captured_session_id`` wins over the bounded-output extractor.

    When the strategy classifies as RESUMABLE_CONTINUE, the producer
    path consults ``opts.captured_session_id`` first and only falls
    back to ``extract_transport_session_id(bounded_output)`` /
    ``extract_transport_session_id_with_visible_tui(line)`` when the
    captured id is None. This pins the precedence so the watchdog-kill
    resume contract (R4) flows the captured id end-to-end.
    """
    strategy = _ProducerStubStrategy()
    handle = _ProducerStubHandle()
    opts = _make_producer_options(strategy, captured_session_id="sess-captured-wins")

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        _check_process_result(
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
