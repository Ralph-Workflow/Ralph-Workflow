from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.process.liveness import FakeLivenessProbe
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path

_HANDLE_PID = 4242


class _PidHandle(_FakeHandle):
    pid = _HANDLE_PID


class _RecordingTeardown:
    def __init__(self) -> None:
        self.calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del owner_label
        self.calls.append((host_pid, issuer))


def _no_signals(
    workspace: object,
    raw_output: object,
    *,
    required_artifact: object = None,
    run_id: object = None,
    sentinel_secret: object = None,
    receipt_secret: object = None,
) -> object:
    return CompletionSignals(False, False, ())


def _run(
    tmp_path: Path, *, descendants_alive: bool
) -> tuple[OpenCodeResumableExitError, _RecordingTeardown]:
    options = CompletionCheckOptions(
        execution_strategy=OpenCodeExecutionStrategy(),
        workspace_path=tmp_path,
        liveness_probe=FakeLivenessProbe(active=descendants_alive),
        required_artifact=RequiredArtifact(
            phase="planning",
            artifact_type="plan",
            artifact_path=".agent/artifacts/plan.md",
            markdown_path=None,
            normalizer=None,
        ),
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            descendant_wait_timeout_seconds=2.0,
            descendant_wait_poll_seconds=0.5,
            parent_exit_grace_seconds=1.0,
        ),
        evaluate_completion_fn=_no_signals,
    )
    teardown = _RecordingTeardown()
    with pytest.raises(OpenCodeResumableExitError) as caught:
        check_process_result(
            _PidHandle(returncode=0, has_descendants=descendants_alive),
            "opencode",
            [],
            options,
            _clock=FakeClock(start=0.0),
            process_teardown=teardown,
        )
    return caught.value, teardown


def test_post_exit_descendant_hang_teardown_issuer_is_post_exit_watchdog(
    tmp_path: Path,
) -> None:
    _error, teardown = _run(tmp_path, descendants_alive=True)

    assert teardown.calls == [(_HANDLE_PID, ProcessTerminationIssuer.POST_EXIT_WATCHDOG)]


def test_quiesced_teardown_issuer_is_invoke_teardown(tmp_path: Path) -> None:
    _error, teardown = _run(tmp_path, descendants_alive=False)

    assert teardown.calls, "quiesced completion must still tear down the process subtree"
    assert {issuer for _pid, issuer in teardown.calls} == {ProcessTerminationIssuer.INVOKE_TEARDOWN}
