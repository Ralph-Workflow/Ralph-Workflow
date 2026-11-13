from __future__ import annotations

import pytest

from ralph.agents.agent_activity_kind import AgentActivityKind
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe_judge import (
    AGENT_RAN_EVIDENCE_KINDS,
    CanaryMarkerState,
    ProbeJudgeOutcome,
    judge_probe,
)

_NO_EVIDENCE_KINDS = (
    AgentActivityKind.OUTPUT_LINE.value,
    AgentActivityKind.LIFECYCLE.value,
    AgentActivityKind.ERROR_LINE.value,
    AgentActivityKind.PROGRESS_REPORT.value,
)

_RAN_EVIDENCE_KINDS = (
    AgentActivityKind.TOOL_USE.value,
    AgentActivityKind.TOOL_RESULT.value,
    AgentActivityKind.STREAM_DELTA.value,
    AgentActivityKind.CHILD_PROCESS.value,
    AgentActivityKind.CHILD_HEARTBEAT.value,
    AgentActivityKind.CHILD_PROGRESS.value,
    AgentActivityKind.CHILD_TERMINAL_ACK.value,
)


def _facts(
    event_kinds: tuple[str, ...] = (),
    tool_calls: tuple[str, ...] = (),
    subagent_activity_observed: bool | None = None,
    liveness_observed: bool | None = None,
    observed_returncode: int | None = None,
) -> AttemptFacts:
    termination = (
        TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=observed_returncode,
        )
        if observed_returncode is not None
        else None
    )
    return AttemptFacts(
        event_kinds=event_kinds,
        tool_calls=tool_calls,
        subagent_activity_observed=subagent_activity_observed,
        liveness_observed=liveness_observed,
        termination=termination,
    )


@pytest.mark.parametrize(
    ("marker_state", "facts", "outcome", "detail"),
    [
        pytest.param(
            CanaryMarkerState.MATCHED,
            None,
            ProbeJudgeOutcome.PASSED,
            "marker_written",
            id="matched_marker_without_facts_passes",
        ),
        pytest.param(
            CanaryMarkerState.MATCHED,
            _facts(),
            ProbeJudgeOutcome.PASSED,
            "marker_written",
            id="matched_marker_with_empty_facts_passes",
        ),
        pytest.param(
            CanaryMarkerState.MATCHED,
            _facts(event_kinds=(AgentActivityKind.TOOL_USE.value,)),
            ProbeJudgeOutcome.PASSED,
            "marker_written",
            id="matched_marker_with_evidence_passes",
        ),
        pytest.param(
            CanaryMarkerState.MISSING,
            _facts(event_kinds=(AgentActivityKind.TOOL_USE.value,)),
            ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK,
            "marker_missing",
            id="missing_marker_with_tool_use_fails",
        ),
        pytest.param(
            CanaryMarkerState.MISMATCHED,
            _facts(event_kinds=(AgentActivityKind.TOOL_RESULT.value,)),
            ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK,
            "marker_content_mismatch",
            id="mismatched_marker_with_tool_result_fails",
        ),
        pytest.param(
            CanaryMarkerState.MISSING,
            _facts(tool_calls=("Bash:ls",)),
            ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK,
            "marker_missing",
            id="missing_marker_with_tool_calls_fails",
        ),
        pytest.param(
            CanaryMarkerState.MISSING,
            _facts(subagent_activity_observed=True),
            ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK,
            "marker_missing",
            id="missing_marker_with_subagent_activity_fails",
        ),
        pytest.param(
            CanaryMarkerState.MISSING,
            _facts(event_kinds=(AgentActivityKind.STREAM_DELTA.value,)),
            ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK,
            "marker_missing",
            id="missing_marker_with_stream_delta_fails",
        ),
        pytest.param(
            CanaryMarkerState.MISSING,
            _facts(event_kinds=_NO_EVIDENCE_KINDS),
            ProbeJudgeOutcome.INCONCLUSIVE,
            "probe_could_not_run",
            id="missing_marker_output_and_lifecycle_only_is_inconclusive",
        ),
        pytest.param(
            CanaryMarkerState.MISMATCHED,
            _facts(
                event_kinds=(
                    AgentActivityKind.ERROR_LINE.value,
                    AgentActivityKind.PROGRESS_REPORT.value,
                ),
                liveness_observed=True,
            ),
            ProbeJudgeOutcome.INCONCLUSIVE,
            "probe_could_not_run",
            id="mismatched_marker_error_liveness_only_is_inconclusive",
        ),
        pytest.param(
            CanaryMarkerState.MISSING,
            None,
            ProbeJudgeOutcome.INCONCLUSIVE,
            "probe_could_not_run",
            id="facts_none_is_inconclusive",
        ),
        pytest.param(
            CanaryMarkerState.MISSING,
            _facts(observed_returncode=1),
            ProbeJudgeOutcome.INCONCLUSIVE,
            "probe_could_not_run",
            id="nonzero_rc_without_evidence_is_inconclusive",
        ),
        pytest.param(
            CanaryMarkerState.MISMATCHED,
            _facts(subagent_activity_observed=False),
            ProbeJudgeOutcome.INCONCLUSIVE,
            "probe_could_not_run",
            id="subagent_activity_false_is_not_evidence",
        ),
    ],
)
def test_judge_probe_table(
    marker_state: CanaryMarkerState,
    facts: AttemptFacts | None,
    outcome: ProbeJudgeOutcome,
    detail: str,
) -> None:
    verdict = judge_probe(marker_state, facts)
    assert verdict.outcome is outcome
    assert verdict.detail == detail
    expected_marker_written = {
        ProbeJudgeOutcome.PASSED: True,
        ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK: False,
        ProbeJudgeOutcome.INCONCLUSIVE: None,
    }[outcome]
    assert verdict.marker_written is expected_marker_written


@pytest.mark.parametrize("kind", _RAN_EVIDENCE_KINDS)
def test_allowlist_covers_agent_ran_kinds(kind: str) -> None:
    assert kind in AGENT_RAN_EVIDENCE_KINDS
    verdict = judge_probe(
        CanaryMarkerState.MISSING,
        _facts(event_kinds=(kind,)),
    )
    assert verdict.outcome is ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK


@pytest.mark.parametrize("kind", _NO_EVIDENCE_KINDS)
def test_allowlist_excludes_non_evidence_kinds(kind: str) -> None:
    assert kind not in AGENT_RAN_EVIDENCE_KINDS


def test_allowlist_does_not_use_enum_members() -> None:
    assert all(isinstance(kind, str) for kind in AGENT_RAN_EVIDENCE_KINDS)
    assert AgentActivityKind.OUTPUT_LINE not in AGENT_RAN_EVIDENCE_KINDS
