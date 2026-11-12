from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    TerminationRecorder,
    attempt_facts_from_exception,
    check_process_result,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.process.liveness import FakeLivenessProbe
from ralph.recovery.attempt_facts import TerminationIssuer
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path


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


def _run(tmp_path: Path, *, descendants_alive: bool) -> OpenCodeResumableExitError:
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
    recorder = TerminationRecorder()
    with pytest.raises(OpenCodeResumableExitError) as caught:
        check_process_result(
            _FakeHandle(returncode=0, has_descendants=descendants_alive),
            "opencode",
            [],
            options,
            _clock=FakeClock(start=0.0),
            termination_recorder=recorder,
        )
    return caught.value


def test_post_exit_descendant_hang_fire_is_attributed_to_the_post_exit_watchdog(
    tmp_path: Path,
) -> None:
    error = _run(tmp_path, descendants_alive=True)

    facts = attempt_facts_from_exception(error)
    assert facts is not None
    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.POST_EXIT_WATCHDOG
    assert facts.termination.liveness_observed is True
    assert facts.liveness_observed is True
    assert error.issuer == TerminationIssuer.POST_EXIT_WATCHDOG.value
    assert error.returncode == 0


def test_resumable_exit_without_a_watchdog_fire_stays_attributed_to_agent_exit(
    tmp_path: Path,
) -> None:
    error = _run(tmp_path, descendants_alive=False)

    facts = attempt_facts_from_exception(error)
    assert facts is not None
    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.AGENT_EXIT
    assert facts.termination.liveness_observed is False
    assert error.issuer == TerminationIssuer.AGENT_EXIT.value
