from __future__ import annotations

import json

from ralph.pipeline.state import PipelineState
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attribution import Attribution, AttributionVerdict, RuntimeCause
from ralph.recovery.dead_agent_verdict import (
    DeadAgentVerdict,
    PersistedVerdict,
    migrate_verdict_record,
    verdict_record,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_verdict_record_newer_than_the_current_schema_is_returned_unchanged": (
        "new coverage: the migration ladder returns a record written by a newer schema unchanged, with its termination log intact"
    ),
    "test_a_verdict_record_marked_as_v1_is_migrated_to_the_current_schema": (
        "new coverage: the v1 rung of the ladder is entered by an explicit version marker as well as by the absence of one"
    ),
    "test_the_state_decoder_keeps_a_newer_than_current_verdict_record_intact": (
        "new coverage: the checkpoint decoder runs the migration ladder on every load, so a newer record must survive the load with its termination log intact"
    ),
    "test_a_verdict_record_with_a_newer_numeric_schema_version_is_returned_unchanged": (
        "new coverage: the schema version marker is compared numerically, so a record "
        "whose marker is a newer JSON number is returned unchanged with its termination "
        "log intact"
    ),
}


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


def _newer_than_current_record() -> dict[str, object]:
    return {
        "schema_version": 3,
        "phase": "planning",
        "agent": "opencode",
        "verdict": "agent_dead",
        "facts": {},
        "attribution": {},
        "probe_outcome": None,
        "termination": {
            "issuer": "post_exit_watchdog",
            "observed_returncode": -15,
            "reported_returncode": 0,
            "liveness_observed": None,
            "return_code_discrepancy": True,
            "log": [{"issuer": "post_exit_watchdog", "source": "descendant_hang"}],
        },
        "typed_signal": None,
        "return_code_discrepancy": True,
    }


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


def test_a_verdict_record_marked_as_v1_is_migrated_to_the_current_schema() -> None:
    v1 = {
        "schema_version": 1,
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


def test_a_verdict_record_newer_than_the_current_schema_is_returned_unchanged() -> None:
    newer = _newer_than_current_record()

    migrated = migrate_verdict_record(newer)

    assert migrated == newer
    assert migrated["schema_version"] == 3
    termination = migrated["termination"]
    assert isinstance(termination, dict)
    assert termination["log"] == [{"issuer": "post_exit_watchdog", "source": "descendant_hang"}]


def test_a_verdict_record_with_a_newer_numeric_schema_version_is_returned_unchanged() -> None:
    newer = _newer_than_current_record()
    newer["schema_version"] = 3.0

    migrated = migrate_verdict_record(newer)

    assert migrated["schema_version"] != 2
    assert migrated["termination"] == newer["termination"]
    termination = migrated["termination"]
    assert isinstance(termination, dict)
    assert termination["log"] == [{"issuer": "post_exit_watchdog", "source": "descendant_hang"}]


def test_the_state_decoder_keeps_a_newer_than_current_verdict_record_intact() -> None:
    newer = _newer_than_current_record()
    state_json = json.dumps({"phase": "planning", "dead_agent_verdict_record": newer})

    state = PipelineState.model_validate_json(state_json)

    record = state.dead_agent_verdict_record
    assert record is not None
    assert record == newer
    termination = record["termination"]
    assert isinstance(termination, dict)
    assert termination["log"] == [{"issuer": "post_exit_watchdog", "source": "descendant_hang"}]
