from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    TerminationRecorder,
)
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.config.general_config import GeneralConfig
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer
from ralph.recovery.attempt_outcome import AttemptOutcome
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.attribution import Attribution, AttributionVerdict
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_verdict import DeadAgentVerdict, PersistedVerdict
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._session_fake_mcp_bridge import _FakeMcpBridge
from tests._session_registry_factory import _RegistryFactory

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.pipeline.attempt_finalizer import AttemptFinalization

PHASE = "development"
AGENT = "claude"
_LIMIT_PHRASE = "You've hit your session limit"


class _InvokeFn(Protocol):
    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> Iterable[object]: ...


class _AttemptFactsCarrier(Protocol):
    attempt_facts: AttemptFacts


def _outcome(
    kind: AttemptOutcomeKind,
    reason: str,
    issuer: TerminationIssuer | None = None,
) -> AttemptOutcome:
    verdict = (
        PersistedVerdict(
            verdict=DeadAgentVerdict.AGENT_DEAD,
            facts=AttemptFacts(artifact_valid=False),
            attribution=AttributionVerdict(
                attribution=Attribution.AGENT,
                counts_toward_agent_state=True,
                reason=reason,
            ),
            probe_outcome=None,
            termination=None,
            typed_signal=None,
            return_code_discrepancy=False,
        )
        if kind is AttemptOutcomeKind.AGENT_DEAD
        else None
    )
    return AttemptOutcome(kind=kind, issuer=issuer, verdict=verdict, reason=reason)


def _recording_finalizer(
    events: list[str],
    outcome: AttemptOutcome,
) -> Callable[[AttemptFinalization], AttemptOutcome]:
    def finalize(finalization: AttemptFinalization) -> AttemptOutcome:
        events.append(f"finalize:{finalization.attempt_ordinal}")
        return outcome

    return finalize


def _failing_then_succeeding_invoke(
    events: list[str],
    failure: Exception,
) -> _InvokeFn:
    calls = {"count": 0}

    def invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> Iterable[object]:
        del config, prompt_file, options
        calls["count"] += 1
        events.append(f"invoke:{calls['count']}")
        if calls["count"] == 1:
            raise failure
        return ['{"type":"result"}']

    return invoke


def _controller(
    *,
    probe_passes: bool = True,
    agent_max_consecutive_unproductive: int | None = None,
) -> RecoveryController:
    def probe(phase: str, agent: str) -> ProbeOutcome | None:
        del phase, agent
        return ProbeOutcome.PASSED if probe_passes else None

    options = RecoveryControllerOptions(dead_agent_probe_runner=probe)
    if agent_max_consecutive_unproductive is not None:
        options = replace(
            options,
            agent_max_consecutive_unproductive=agent_max_consecutive_unproductive,
        )
    return RecoveryController(options=options)


def _run_effect(
    tmp_path: Path,
    invoke: _InvokeFn,
    *,
    attempt_finalizer: Callable[[AttemptFinalization], AttemptOutcome] | None = None,
    controller: RecoveryController | None = None,
) -> PipelineEvent | object:
    display_context = make_display_context()
    _RegistryFactory._agent_config = AgentConfig(cmd=AGENT, output_flag="--json-stream")
    deps = make_test_pipeline_deps(
        display_context,
        bridge=_FakeMcpBridge(),
        registry_factory=_RegistryFactory.from_config,
    )
    if controller is not None:
        deps = replace(deps, recovery_controller=controller)
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")
    return effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(agent_name=AGENT, phase=PHASE, prompt_file=str(prompt_file)),
        UnifiedConfig(general=GeneralConfig(max_same_agent_retries=2)),
        deps,
        WorkspaceScope(tmp_path),
        display_context=display_context,
        invoke_agent=invoke,
        agent_invocation_error=AgentInvocationError,
        attempt_finalizer=attempt_finalizer,
    )


def _inactivity_failure() -> AgentInactivityTimeoutError:
    return AgentInactivityTimeoutError(
        agent_name=AGENT,
        timeout_seconds=30.0,
        parsed_output=[],
        opts=InactivityTimeoutOpts(session_resume_safe=True, resumable_session_id="sess-1"),
    )


def test_in_session_retry_finalizes_the_failed_attempt_once_before_the_next_attempt(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    finalizer = _recording_finalizer(
        events,
        _outcome(AttemptOutcomeKind.RETRYABLE_FAILURE, "an inactivity timeout"),
    )

    result = _run_effect(
        tmp_path,
        _failing_then_succeeding_invoke(events, _inactivity_failure()),
        attempt_finalizer=finalizer,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert events == ["invoke:1", "finalize:1", "invoke:2"], (
        f"the failed attempt must be finalized exactly once before the retry starts; {events=}"
    )


def test_fallback_finalizer_charges_the_ledger_once_for_the_failed_attempt(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    controller = _controller(agent_max_consecutive_unproductive=2)

    result = _run_effect(
        tmp_path,
        _failing_then_succeeding_invoke(events, _inactivity_failure()),
        controller=controller,
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert events == ["invoke:1", "invoke:2"], (
        f"the retry must follow the failed attempt; {events=}"
    )
    status = controller.ledger.status(PHASE, AGENT)
    assert status.is_eligible, (
        "the failed attempt must be charged to the ledger exactly once and the "
        "successful retry must clear it"
    )


def test_runtime_ended_outcome_stops_the_same_agent_retry(tmp_path: Path) -> None:
    events: list[str] = []
    finalizer = _recording_finalizer(
        events,
        _outcome(
            AttemptOutcomeKind.RUNTIME_ENDED,
            "runtime_issued_termination_is_not_agent_fault",
            TerminationIssuer.SUPERVISOR_IDLE,
        ),
    )

    _run_effect(
        tmp_path,
        _failing_then_succeeding_invoke(events, _inactivity_failure()),
        attempt_finalizer=finalizer,
    )

    assert events == ["invoke:1", "finalize:1"], (
        f"a runtime-ended outcome must not start a second attempt; {events=}"
    )


def test_agent_dead_outcome_stops_the_same_agent_retry(tmp_path: Path) -> None:
    events: list[str] = []
    finalizer = _recording_finalizer(
        events,
        _outcome(AttemptOutcomeKind.AGENT_DEAD, "authoritative_typed_signal"),
    )

    _run_effect(
        tmp_path,
        _failing_then_succeeding_invoke(events, _inactivity_failure()),
        attempt_finalizer=finalizer,
    )

    assert events == ["invoke:1", "finalize:1"], (
        f"an agent-dead outcome must not start a second attempt; {events=}"
    )


def test_quota_like_words_without_a_typed_signal_never_skip_bench_or_terminate(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    controller = _controller()
    failure = AgentInvocationError(AGENT, 1, f"provider said: {_LIMIT_PHRASE}")

    _run_effect(
        tmp_path,
        _failing_then_succeeding_invoke(events, failure),
        controller=controller,
    )

    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons")
    assert not reasons, f"quota-like words must never bench an agent; {reasons=}"
    status = controller.ledger.status(PHASE, AGENT)
    assert status.is_eligible, "quota-like words must never terminate a session"


def test_failure_log_line_shows_the_issuer_and_both_return_codes(tmp_path: Path) -> None:
    messages: list[str] = []
    sink_id = logger.add(_capture_message(messages), level="ERROR")
    failure = AgentInvocationError(AGENT, 9, "fatal: unrelated failure")
    recorder = TerminationRecorder()
    recorder.record_issuer(TerminationIssuer.AGENT_EXIT)
    recorder.record_observed_returncode(7)
    recorder.record_reported_returncode(9)
    recorder.record_exit_status("agent")
    cast("_AttemptFactsCarrier", failure).attempt_facts = recorder.facts()
    try:
        _run_effect(tmp_path, _failing_then_succeeding_invoke([], failure))
    finally:
        logger.remove(sink_id)

    failure_lines = [line for line in messages if "Agent invocation failed:" in line]
    assert failure_lines, f"the failure log line must be emitted; {messages=}"
    assert "issuer=agent_exit" in failure_lines[0]
    assert "observed_rc=7" in failure_lines[0]
    assert "reported_rc=9" in failure_lines[0]


def _capture_message(messages: list[str]) -> Callable[[object], None]:
    def capture(message: object) -> None:
        messages.append(str(message))

    return capture
