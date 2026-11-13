from __future__ import annotations

from ralph.agents.invoke import (
    TerminationRecorder,
    termination_issuer_for_teardown_issuer,
)
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attempt_failure_log_line import render_attempt_failure_log_line


def test_agent_exit_followed_by_post_exit_watchdog_resolves_to_watchdog() -> None:
    recorder = TerminationRecorder()

    recorder.record_issuer(TerminationIssuer.AGENT_EXIT, source="process_exit")
    recorder.record_issuer(TerminationIssuer.POST_EXIT_WATCHDOG, source="descendant_hang")

    termination = recorder.termination()
    assert termination.issuer is TerminationIssuer.POST_EXIT_WATCHDOG
    assert [(entry.issuer, entry.source) for entry in termination.log] == [
        (TerminationIssuer.AGENT_EXIT, "process_exit"),
        (TerminationIssuer.POST_EXIT_WATCHDOG, "descendant_hang"),
    ]


def test_first_runtime_termination_entry_wins() -> None:
    recorder = TerminationRecorder()

    recorder.record_issuer(TerminationIssuer.QUOTA_DETECTOR, source="quota")
    recorder.record_issuer(TerminationIssuer.OPERATOR, source="shutdown")

    assert recorder.termination().issuer is TerminationIssuer.QUOTA_DETECTOR


def test_empty_termination_log_resolves_to_unknown() -> None:
    assert TerminationRecorder().termination().issuer is TerminationIssuer.UNKNOWN


def test_teardown_issuer_mapping_covers_process_issuer_values() -> None:
    expected = {
        "supervisor_idle": TerminationIssuer.SUPERVISOR_IDLE,
        "quota_detector": TerminationIssuer.QUOTA_DETECTOR,
        "post_exit_watchdog": TerminationIssuer.POST_EXIT_WATCHDOG,
        "recovery_timeout": TerminationIssuer.RECOVERY_TIMEOUT,
        "operator": TerminationIssuer.OPERATOR,
        "broken_agent_timer": TerminationIssuer.SUPERVISOR_IDLE,
        "context_exit": TerminationIssuer.OPERATOR,
        "invoke_teardown": TerminationIssuer.OPERATOR,
        "call_timeout": TerminationIssuer.OPERATOR,
        "output_limit": TerminationIssuer.OPERATOR,
        "tool_timeout": TerminationIssuer.UNKNOWN,
        "mcp_shutdown": TerminationIssuer.UNKNOWN,
        "git_timeout": TerminationIssuer.UNKNOWN,
        "executor_cancel": TerminationIssuer.UNKNOWN,
        "test_runner": TerminationIssuer.UNKNOWN,
        "diagnostics": TerminationIssuer.UNKNOWN,
    }

    assert {value: termination_issuer_for_teardown_issuer(value) for value in expected} == expected


def test_failure_line_reads_origin_issuer_and_codes_only_from_facts() -> None:
    facts = AttemptFacts(
        exit_status="intentional_termination",
        termination=TerminationRecord.create(
            TerminationIssuer.POST_EXIT_WATCHDOG,
            observed_returncode=-15,
            reported_returncode=0,
        ),
    )

    line = render_attempt_failure_log_line(facts, "session ended")

    assert line == (
        "origin=intentional_termination issuer=post_exit_watchdog "
        "observed_rc=-15 reported_rc=0 session ended"
    )


def test_failure_line_does_not_read_exception_attributes() -> None:
    class MisleadingMessage(str):
        failure_origin = "agent"
        issuer = "agent_exit"
        returncode = 99

    facts = AttemptFacts(
        exit_status="runtime_launch",
        termination=TerminationRecord.create(
            TerminationIssuer.OPERATOR,
            observed_returncode=-15,
            reported_returncode=143,
        ),
    )

    line = render_attempt_failure_log_line(facts, MisleadingMessage("failed"))

    assert line == ("origin=runtime_launch issuer=operator observed_rc=-15 reported_rc=143 failed")
