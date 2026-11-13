from __future__ import annotations

from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attribution import Attribution, AttributionVerdict, RuntimeCause
from ralph.recovery.dead_agent_verdict import (
    DeadAgentVerdict,
    PersistedVerdict,
    migrate_verdict_record,
    verdict_record,
)


def _persisted_verdict() -> PersistedVerdict:
    termination = TerminationRecord.create(
        TerminationIssuer.POST_EXIT_WATCHDOG,
        observed_returncode=-15,
        reported_returncode=0,
        source="descendant_hang",
    )
    facts = AttemptFacts(exit_status="intentional_termination", termination=termination)
    return PersistedVerdict(
        verdict=DeadAgentVerdict.NOT_AGENT_FAULT,
        facts=facts,
        attribution=AttributionVerdict(
            attribution=Attribution.RUNTIME,
            runtime_cause=RuntimeCause.KILL_POST_EXIT_WATCHDOG,
            cleared=True,
            counts_toward_agent_state=False,
            reason="runtime_issued_termination",
        ),
        probe_outcome=None,
        termination=termination,
        typed_signal=None,
        return_code_discrepancy=True,
    )


def test_verdict_record_schema_v2_carries_termination_log_and_discrepancy() -> None:
    record = verdict_record("planning", "opencode", _persisted_verdict())

    assert set(record) == {
        "schema_version",
        "phase",
        "agent",
        "verdict",
        "facts",
        "attribution",
        "probe_outcome",
        "termination",
        "typed_signal",
        "return_code_discrepancy",
    }
    assert record["schema_version"] == 2
    assert record["return_code_discrepancy"] is True
    assert record["termination"] == {
        "issuer": "post_exit_watchdog",
        "observed_returncode": -15,
        "reported_returncode": 0,
        "liveness_observed": None,
        "return_code_discrepancy": True,
        "log": [{"issuer": "post_exit_watchdog", "source": "descendant_hang"}],
    }


def test_v1_verdict_record_migrates_to_schema_v2() -> None:
    v1 = {
        "phase": "planning",
        "agent": "opencode",
        "verdict": "not_agent_fault",
        "termination": {
            "issuer": "operator",
            "observed_returncode": -15,
            "reported_returncode": 143,
            "liveness_observed": True,
            "return_code_discrepancy": True,
        },
    }

    migrated = migrate_verdict_record(v1)

    assert migrated["schema_version"] == 2
    assert migrated["termination"] == {
        **v1["termination"],
        "log": [{"issuer": "operator", "source": "v1"}],
    }


def test_migrating_a_v2_verdict_record_leaves_it_unchanged() -> None:
    v2 = verdict_record("planning", "opencode", _persisted_verdict())

    assert migrate_verdict_record(v2) == v2
