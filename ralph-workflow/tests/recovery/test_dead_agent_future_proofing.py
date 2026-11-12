from __future__ import annotations

import functools
import json
import os
import re
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from ralph.agents.invoke import (
    AgentInvocationError,
    attempt_facts_for_failure,
    is_provider_quota_output_line,
    termination_issuer_for_exception,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.timeout_defaults import (
    AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT,
    AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT,
)
from tests.recovery._scenario_harness import _build_policy_bundle

if TYPE_CHECKING:
    from collections.abc import Callable

PHASE = "development"
AGENT = "agent_a"
DEAD = str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)
PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "ralph"
SLOW = settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

HEALTHY_AGENT_OUTCOMES = (ProbeOutcome.PASSED, ProbeOutcome.INCONCLUSIVE, None)
ANY_RETURN_CODE = st.one_of(
    st.none(),
    st.sampled_from([0, 1, 2, 126, 127, 128, 129, 130, 137, 139, 143, -1, -9, -11, -15]),
    st.integers(min_value=-300, max_value=300),
)
WORDING = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=0x2FF, blacklist_categories=("Cs",)),
    max_size=80,
)
QUOTA_LIKE = st.sampled_from(
    [
        "You've hit your session limit",
        "Credit balance is too low",
        "insufficient_quota",
        "rate limit reached",
        "RESOURCE_EXHAUSTED (code 429)",
        "out of credits",
        "quota exceeded",
        "credits exhausted",
    ]
)
CONTENT_KEYS = st.sampled_from(
    ["text", "content", "message", "output", "result", "prompt", "thinking", "stdout"]
)
UNSEEN_EVENT_TYPES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz._-", min_size=1, max_size=18)
NON_PROVIDER_TYPES = st.sampled_from(
    ["reasoning", "subagent", "thinking", "tool", "tool_result", "tool_use", "user"]
)


def _controller(
    runner: Callable[[str, str], ProbeOutcome | None] | None = None,
) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            dead_agent_probe_runner=runner,
        )
    )


def _state() -> PipelineState:
    return PipelineState(
        phase=PHASE,
        phase_chains={PHASE: AgentChainState(agents=[AGENT, "peer"], current_index=0, retries=0)},
        last_connectivity_state="online",
    )


def _ended(controller: RecoveryController, exc: BaseException, facts: AttemptFacts | None) -> None:
    controller.handle(
        _state(),
        exc,
        FailureContext(phase=PHASE, agent=AGENT, facts=facts),
    )


def _benched(controller: RecoveryController) -> bool:
    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons", {})
    return isinstance(reasons, dict) and reasons.get(AGENT) == DEAD


def _is_clean(controller: RecoveryController) -> bool:
    return (
        not _benched(controller)
        and not controller.ledger.status(PHASE, AGENT).is_spent
        and not controller.snapshot()["spent_agents"]
    )


def _live_facts(
    issuer: TerminationIssuer,
    observed: int | None,
    reported: int | None,
    tool_calls: tuple[str, ...],
    kinds: tuple[str, ...],
    workspace: bool | None,
) -> AttemptFacts:
    return AttemptFacts(
        event_kinds=kinds,
        tool_calls=tool_calls,
        artifact_valid=False,
        workspace_changed=workspace,
        liveness_observed=True,
        termination=TerminationRecord.create(
            issuer,
            observed_returncode=observed,
            reported_returncode=reported,
            liveness_observed=True,
        ),
    )


@SLOW
@given(
    issuer=st.sampled_from(list(TerminationIssuer)),
    observed=ANY_RETURN_CODE,
    reported=ANY_RETURN_CODE,
    tool_calls=st.lists(WORDING.filter(bool), min_size=1, max_size=4),
    kinds=st.lists(WORDING, max_size=5),
    workspace=st.one_of(st.none(), st.booleans()),
    wording=WORDING,
    quota_phrase=QUOTA_LIKE,
    code=ANY_RETURN_CODE,
    probe=st.sampled_from(HEALTHY_AGENT_OUTCOMES),
)
def test_a_live_session_that_ends_for_any_reason_is_never_benched_or_spent(
    issuer: TerminationIssuer,
    observed: int | None,
    reported: int | None,
    tool_calls: list[str],
    kinds: list[str],
    workspace: bool | None,
    wording: str,
    quota_phrase: str,
    code: int | None,
    probe: ProbeOutcome | None,
) -> None:
    controller = _controller(lambda _phase, _agent: probe)
    exc = AgentInvocationError(
        AGENT,
        code if code is not None else 0,
        stderr=wording or "ended",
        parsed_output=[quota_phrase, json.dumps({"type": "tool_result", "content": quota_phrase})],
    )

    _ended(
        controller,
        exc,
        _live_facts(issuer, observed, reported, tuple(tool_calls), tuple(kinds), workspace),
    )

    assert _is_clean(controller)


@SLOW
@given(
    wording=WORDING.filter(lambda text: bool(text.strip())),
    code=ANY_RETURN_CODE,
    tool_text=st.lists(QUOTA_LIKE, max_size=4),
    probe=st.sampled_from(HEALTHY_AGENT_OUTCOMES),
    attach_facts=st.booleans(),
    with_runner=st.booleans(),
)
def test_an_end_in_an_unseen_format_never_benches_without_failed_probe_evidence(
    wording: str,
    code: int | None,
    tool_text: list[str],
    probe: ProbeOutcome | None,
    attach_facts: bool,
    with_runner: bool,
) -> None:
    controller = _controller((lambda _phase, _agent: probe) if with_runner else None)
    exc = AgentInvocationError(
        AGENT,
        code if code is not None else 0,
        stderr=re.sub(r"\s+", " ", wording).strip(),
        parsed_output=[json.dumps({"type": "unseen", "text": text}) for text in tool_text],
    )

    _ended(controller, exc, attempt_facts_for_failure(exc) if attach_facts else None)

    assert _is_clean(controller)


@SLOW
@given(code=ANY_RETURN_CODE, wording=WORDING)
def test_every_ended_attempt_records_an_issuer_and_never_defaults_a_possible_kill_to_agent_exit(
    code: int | None, wording: str
) -> None:
    reported = code if code is not None else 0
    exc = AgentInvocationError(AGENT, reported, stderr=wording)

    facts = attempt_facts_for_failure(exc)
    issuer = termination_issuer_for_exception(exc)

    assert facts.termination is not None
    assert isinstance(facts.termination.issuer, TerminationIssuer)
    signal_like = reported < 0 or 129 <= reported <= 159
    if signal_like:
        assert issuer is TerminationIssuer.UNKNOWN
    if issuer is TerminationIssuer.AGENT_EXIT:
        assert not signal_like


@SLOW
@given(
    event_type=st.one_of(NON_PROVIDER_TYPES, UNSEEN_EVENT_TYPES),
    key=CONTENT_KEYS,
    phrase=QUOTA_LIKE,
    nested=st.booleans(),
    null_error=st.booleans(),
)
def test_quota_like_words_in_tool_reasoning_subagent_or_unseen_content_never_terminate(
    event_type: str, key: str, phrase: str, nested: bool, null_error: bool
) -> None:
    assume(event_type.casefold() != "error" and not event_type.casefold().startswith("item."))
    body: dict[str, object] = {key: phrase}
    event: dict[str, object] = {"type": event_type, **({"part": body} if nested else body)}
    if null_error:
        event["error"] = None

    assert is_provider_quota_output_line(json.dumps(event)) is False


@SLOW
@given(phrase=QUOTA_LIKE, wrapper=st.sampled_from(["user", "human", "system_prompt"]))
def test_a_user_prompt_quoting_quota_words_never_terminates(phrase: str, wrapper: str) -> None:
    line = json.dumps({"type": wrapper, "message": {"role": "user", "content": phrase}})

    assert is_provider_quota_output_line(line) is False


@pytest.mark.parametrize("probe", [ProbeOutcome.PASSED, ProbeOutcome.INCONCLUSIVE, None])
@SLOW
@given(
    events_a=st.lists(WORDING, max_size=5),
    events_b=st.lists(WORDING, max_size=5),
    message_a=WORDING,
    message_b=WORDING,
)
def test_the_verdict_for_an_unproven_probe_is_independent_of_transcript_wording(
    probe: ProbeOutcome | None,
    events_a: list[str],
    events_b: list[str],
    message_a: str,
    message_b: str,
) -> None:
    outcomes: list[bool] = []
    for events, message in ((events_a, message_a), (events_b, message_b)):
        controller = _controller(lambda _phase, _agent: probe)
        facts = AttemptFacts(
            event_kinds=tuple(events),
            artifact_valid=False,
            workspace_changed=False,
            termination=TerminationRecord.create(
                TerminationIssuer.AGENT_EXIT, observed_returncode=0, reported_returncode=0
            ),
        )
        _ended(controller, AgentInvocationError(AGENT, 0, stderr=message or "x"), facts)
        outcomes.append(_benched(controller))

    assert outcomes == [False, False]


@SLOW
@given(events=st.lists(WORDING, max_size=5), message=WORDING)
def test_a_failed_probe_benches_the_inert_agent_whatever_the_transcript_says(
    events: list[str], message: str
) -> None:
    controller = _controller(lambda _phase, _agent: ProbeOutcome.FAILED_REPRODUCING_NO_WORK)
    facts = AttemptFacts(
        event_kinds=tuple(
            kind for kind in events if kind.strip() not in {"tool_use", "tool_result"}
        ),
        artifact_valid=False,
        workspace_changed=False,
        liveness_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT, observed_returncode=0, reported_returncode=0
        ),
    )

    _ended(controller, AgentInvocationError(AGENT, 0, stderr=message or "x"), facts)

    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons", {})
    assert isinstance(reasons, dict)
    assert reasons.get(AGENT) == DEAD


def test_a_crashing_probe_runner_never_benches_and_never_breaks_recovery() -> None:
    def crashing(_phase: str, _agent: str) -> ProbeOutcome | None:
        raise RuntimeError("probe infrastructure exploded")

    controller = _controller(crashing)
    facts = AttemptFacts(artifact_valid=False, workspace_changed=False)

    _ended(controller, AgentInvocationError(AGENT, 0, stderr="boom"), facts)

    assert _is_clean(controller)


def test_an_exhausted_probe_budget_never_benches() -> None:
    controller = _controller(lambda _phase, _agent: None)
    facts = AttemptFacts(artifact_valid=False, workspace_changed=False)

    _ended(controller, AgentInvocationError(AGENT, 0, stderr="boom"), facts)

    assert _is_clean(controller)


@functools.cache
def _package_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for directory, _names, files in os.walk(PACKAGE_ROOT):
        relative_dir = Path(directory).relative_to(PACKAGE_ROOT)
        if "testing" in relative_dir.parts:
            continue
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = Path(directory) / filename
            text = SourceFileLoader(path.stem, str(path)).get_source(path.stem)
            sources[str(relative_dir / filename)] = text or ""
    return sources


def _files_containing(pattern: str) -> set[str]:
    compiled = re.compile(pattern)
    return {name for name, text in _package_sources().items() if compiled.search(text)}


def test_only_the_recovery_controller_benches_agents_as_dead() -> None:
    assert _files_containing(r"DEAD_AGENT_NO_PROGRESS") == {
        "recovery/controller.py",
        "recovery/unavailability_reason.py",
    }
    assert _files_containing(r"record_confirmed_unavailability\(") == {"recovery/controller.py"}
    assert _files_containing(r"\.mark_unavailable\(") == {"recovery/controller.py"}


def test_spent_state_is_only_written_through_the_ledger_threshold_outcome() -> None:
    assert _files_containing(r"note_retry_exhaustion\(") == {"recovery/controller.py"}
    assert _files_containing(r"_spent_agents\b") == {"recovery/controller.py"}
    controller_text = _package_sources()["recovery/controller.py"]
    writes = [
        line.strip()
        for line in controller_text.splitlines()
        if re.search(r"_spent_agents\.setdefault\(", line)
    ]
    assert writes == ["self._spent_agents.setdefault(phase, set()).add(agent)"]
    assert controller_text.count("self.note_retry_exhaustion(phase, agent)") == 1
    assert "if outcome.status.is_spent:" in controller_text


def test_nothing_sets_the_per_call_current_allowance_spent_hint() -> None:
    assert _files_containing(r"current_allowance_spent") == set()


def test_ledger_thresholds_are_unchanged() -> None:
    assert AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT == 3
    assert AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT == 9


def test_no_detector_scans_raw_lines_with_the_bare_phrase_matcher() -> None:
    bare_matcher_users = _files_containing(
        r"_is_subscription_limit_message\(\[(line|queued_line)\]"
    )

    assert bare_matcher_users <= {"agents/invoke/_quota_detection.py"}


def test_a_failed_attempt_on_a_one_agent_chain_with_no_evidence_is_an_ordinary_failure() -> None:
    bundle = _build_policy_bundle([AGENT])
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=FakeClock(start=0.0), policy_bundle=bundle)
    )

    _ended(controller, AgentInvocationError(AGENT, 0, stderr="ended"), None)

    assert _is_clean(controller)
