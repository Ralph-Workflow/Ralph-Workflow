from __future__ import annotations

from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer

__all__ = ("render_attempt_failure_log_line",)


def render_attempt_failure_log_line(facts: AttemptFacts, message: str) -> str:
    termination = facts.termination
    issuer = termination.issuer if termination is not None else TerminationIssuer.UNKNOWN
    observed_returncode = termination.observed_returncode if termination is not None else None
    reported_returncode = termination.reported_returncode if termination is not None else None
    origin = facts.exit_status or "unknown"
    return (
        f"origin={origin} issuer={issuer.value} observed_rc={observed_returncode} "
        f"reported_rc={reported_returncode} {message}"
    )
