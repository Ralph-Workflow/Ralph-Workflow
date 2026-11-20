from __future__ import annotations

import json
from pathlib import Path

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.checkpoint import load as load_checkpoint
from ralph.pipeline.events import AgentFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attribution import Attribution, AttributionVerdict, RuntimeCause
from ralph.recovery.canary_probe import ProbeOutcome, ProbeResult
from ralph.recovery.controller import RecoveryController
from ralph.recovery.dead_agent_verdict import (
    DeadAgentVerdict,
    PersistedVerdict,
    decide_dead_agent_verdict,
    migrate_verdict_record,
    verdict_record,
)
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_controller_finalization_persists_probe_reason_and_elapsed_ms_from_probe_runner": (
        "pins that the controller's finalize_attempt plumbing preserves the probe reason "
        "and elapsed time returned by the probe runner end-to-end into the persisted verdict record"
    ),
    "test_a_verdict_record_newer_than_the_current_schema_is_returned_unchanged": (
        "new coverage: the migration ladder returns a record written by a newer schema "
        "unchanged, with its termination log intact; the current schema version is 3, so "
        "the newer record carries the version marker 4 and the assertion pins that marker"
    ),
    "test_a_verdict_record_marked_as_v1_is_migrated_to_the_current_schema": (
        "new coverage: the v1 rung of the ladder is entered by an explicit version marker "
        "as well as by the absence of one; the ladder grew a second rung, so the migrated "
        "marker is the current schema version 3 instead of version 2"
    ),
    "test_the_state_decoder_keeps_a_newer_than_current_verdict_record_intact": (
        "new coverage: the checkpoint decoder runs the migration ladder on every load, so a newer record must survive the load with its termination log intact"
    ),
    "test_a_verdict_record_with_a_newer_numeric_schema_version_is_returned_unchanged": (
        "new coverage: the schema version marker is compared numerically, so a record "
        "whose marker is a newer JSON number is returned unchanged with its termination "
        "log intact; the current schema version is 3, so the newer numeric marker is 4.0 "
        "and the assertion pins that the marker is not the current version"
    ),
    "test_verdict_record_schema_v2_carries_termination_log_and_discrepancy": (
        "the persisted verdict record moved from schema_version 2 to schema_version 3 when "
        "the canary probe reason and its elapsed milliseconds joined the record, so the "
        "expected key set gains probe_reason and probe_elapsed_ms and the version marker is 3"
    ),
    "test_v1_verdict_record_migrates_to_schema_v2": (
        "the migration ladder grew a second rung, so a version-1 record now walks through "
        "version 2 to the current schema version 3 instead of stopping at version 2"
    ),
    "test_migrating_a_v2_verdict_record_leaves_it_unchanged": (
        "the record the ladder now receives is written at schema version 3, so the test "
        "pins the current marker beside the unchanged round trip"
    ),
    "test_a_v2_verdict_record_loaded_from_a_checkpoint_migrates_to_the_current_schema": (
        "new coverage: the migration ladder was only pinned through a direct call to the "
        "migration helper, so the checkpoint load route was unproven; the state validator "
        "runs the ladder on every load, so a version-2 record loaded from a checkpoint must "
        "reach schema version 3 with an empty probe reason and no elapsed milliseconds while "
        "every other field survives the load unchanged"
    ),
    "test_the_reducer_persists_the_probe_reason_and_elapsed_ms_into_the_pipeline_state": (
        "new coverage: the probe reason and the elapsed milliseconds were only pinned on the "
        "controller's own verdict record, so the reducer route into the pipeline state was "
        "unproven; the reducer copies the controller's record into the state, so the persisted "
        "state record must carry the same probe evidence and schema version"
    ),
}


def _attempt_facts() -> AttemptFacts:
    termination = TerminationRecord.create(
        TerminationIssuer.POST_EXIT_WATCHDOG,
        observed_returncode=-15,
        reported_returncode=0,
        source="descendant_hang",
    )
    return AttemptFacts(exit_status="intentional_termination", termination=termination)


def _runtime_attribution() -> AttributionVerdict:
    return AttributionVerdict(
        attribution=Attribution.RUNTIME,
        runtime_cause=RuntimeCause.KILL_POST_EXIT_WATCHDOG,
        cleared=True,
        counts_toward_agent_state=False,
        reason="runtime_issued_termination",
    )


def _persisted_verdict() -> PersistedVerdict:
    facts = _attempt_facts()
    return PersistedVerdict(
        verdict=DeadAgentVerdict.NOT_AGENT_FAULT,
        facts=facts,
        attribution=_runtime_attribution(),
        probe_outcome=None,
        termination=facts.termination,
        typed_signal=None,
        return_code_discrepancy=True,
    )


def _newer_than_current_record() -> dict[str, object]:
    return {
        "schema_version": 4,
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


def _v2_verdict_record() -> dict[str, object]:
    record = dict(verdict_record("planning", "opencode", _persisted_verdict()))
    record["schema_version"] = 2
    record.pop("probe_reason")
    record.pop("probe_elapsed_ms")
    return record


def _pipeline_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="planning",
                role="terminal",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="planning",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
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
        "probe_reason",
        "probe_elapsed_ms",
        "termination",
        "typed_signal",
        "return_code_discrepancy",
    }
    assert record["schema_version"] == 3
    assert record["probe_reason"] == ""
    assert record["probe_elapsed_ms"] is None
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

    assert migrated["schema_version"] == 3
    assert migrated["probe_reason"] == ""
    assert migrated["probe_elapsed_ms"] is None
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

    assert migrated["schema_version"] == 3
    assert migrated["termination"] == {
        **v1["termination"],
        "log": [{"issuer": "operator", "source": "v1"}],
    }


def test_migrating_a_v2_verdict_record_leaves_it_unchanged() -> None:
    v2 = verdict_record("planning", "opencode", _persisted_verdict())

    assert v2["schema_version"] == 3
    assert migrate_verdict_record(v2) == v2


def test_a_v2_verdict_record_migrates_to_the_current_schema_with_the_probe_evidence() -> None:
    v2 = {
        "schema_version": 2,
        "phase": "planning",
        "agent": "opencode",
        "verdict": "not_agent_fault",
        "facts": {"exit_status": "intentional_termination"},
        "attribution": {"attribution": "runtime"},
        "probe_outcome": None,
        "termination": {
            "issuer": "operator",
            "observed_returncode": -15,
            "reported_returncode": 143,
            "liveness_observed": True,
            "return_code_discrepancy": True,
            "log": [{"issuer": "operator", "source": "v1"}],
        },
        "typed_signal": None,
        "return_code_discrepancy": True,
    }

    migrated = migrate_verdict_record(v2)

    assert migrated["schema_version"] == 3
    assert migrated["probe_reason"] == ""
    assert migrated["probe_elapsed_ms"] is None
    assert set(migrated) == set(v2) | {"probe_reason", "probe_elapsed_ms"}
    for key, value in v2.items():
        if key == "schema_version":
            continue
        assert migrated[key] == value


def test_a_v3_verdict_record_survives_the_migration_ladder_unchanged() -> None:
    record = verdict_record("planning", "opencode", _persisted_verdict())

    assert record["schema_version"] == 3
    assert migrate_verdict_record(record) == record


def test_the_verdict_record_persists_the_probe_reason_and_its_elapsed_ms() -> None:
    verdict = decide_dead_agent_verdict(
        _attempt_facts(),
        _runtime_attribution(),
        ProbeOutcome.INCONCLUSIVE,
        probe_reason="probe_timeout",
        probe_elapsed_ms=1234,
    )

    record = verdict_record("planning", "opencode", verdict)

    assert record["probe_reason"] == "probe_timeout"
    assert record["probe_elapsed_ms"] == 1234


def test_controller_finalization_persists_probe_reason_and_elapsed_ms_from_probe_runner() -> None:
    clock = FakeClock()
    probe_result = ProbeResult(
        outcome=ProbeOutcome.INCONCLUSIVE,
        reason="probe_terminated_by_runtime:supervisor_idle:rc=9",
        elapsed_ms=456,
        detail="idle timeout",
    )
    options = RecoveryControllerOptions(
        clock=clock,
        dead_agent_probe_runner=lambda phase, agent: probe_result,
    )
    controller = RecoveryController(options=options)
    facts = AttemptFacts(
        exit_status="0",
        termination=TerminationRecord.create(
            TerminationIssuer.UNKNOWN,
            observed_returncode=0,
            reported_returncode=0,
        ),
    )
    controller.finalize_attempt("planning", "opencode", facts=facts, attempt_failed=True)

    record = controller.last_verdict_record
    assert record is not None
    assert record["schema_version"] == 3
    assert record["probe_reason"] == "probe_terminated_by_runtime:supervisor_idle:rc=9"
    assert record["probe_elapsed_ms"] == 456
    assert record["probe_outcome"] == "inconclusive"


def test_a_verdict_record_newer_than_the_current_schema_is_returned_unchanged() -> None:
    newer = _newer_than_current_record()

    migrated = migrate_verdict_record(newer)

    assert migrated == newer
    assert migrated["schema_version"] == 4
    termination = migrated["termination"]
    assert isinstance(termination, dict)
    assert termination["log"] == [{"issuer": "post_exit_watchdog", "source": "descendant_hang"}]


def test_a_verdict_record_with_a_newer_numeric_schema_version_is_returned_unchanged() -> None:
    newer = _newer_than_current_record()
    newer["schema_version"] = 4.0

    migrated = migrate_verdict_record(newer)

    assert migrated["schema_version"] != 3
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


def test_a_v2_verdict_record_loaded_from_a_checkpoint_migrates_to_the_current_schema(
    tmp_path: Path,
) -> None:
    v2 = _v2_verdict_record()
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps({"phase": "planning", "dead_agent_verdict_record": v2}),
        encoding="utf-8",
    )

    state = load_checkpoint(checkpoint_path)

    assert state is not None
    record = state.dead_agent_verdict_record
    assert record is not None
    assert record["schema_version"] == 3
    assert record["probe_reason"] == ""
    assert record["probe_elapsed_ms"] is None
    assert set(record) == set(v2) | {"probe_reason", "probe_elapsed_ms"}
    for key, value in v2.items():
        if key == "schema_version":
            continue
        assert record[key] == value
    validated = PipelineState.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))
    assert validated.dead_agent_verdict_record == record


def test_the_reducer_persists_the_probe_reason_and_elapsed_ms_into_the_pipeline_state() -> None:
    clock = FakeClock()
    probe_result = ProbeResult(
        outcome=ProbeOutcome.INCONCLUSIVE,
        reason="probe_terminated_by_runtime:supervisor_idle:rc=9",
        elapsed_ms=456,
        detail="idle timeout",
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            dead_agent_probe_runner=lambda phase, agent: probe_result,
        )
    )
    facts = AttemptFacts(
        exit_status="0",
        termination=TerminationRecord.create(
            TerminationIssuer.UNKNOWN,
            observed_returncode=0,
            reported_returncode=0,
        ),
    )
    state = PipelineState(
        phase="planning",
        phase_chains={
            "planning": AgentChainState(
                agents=["opencode", "peer"],
                current_index=0,
                retries=0,
            )
        },
    )
    event = AgentFailureEvent(phase="planning", agent="opencode", facts=facts)

    reduced, _effects = reduce(state, event, _pipeline_policy(), recovery=controller)

    record = reduced.dead_agent_verdict_record
    assert record is not None
    assert record["schema_version"] == 3
    assert record["probe_reason"] == "probe_terminated_by_runtime:supervisor_idle:rc=9"
    assert record["probe_elapsed_ms"] == 456
    assert record == controller.last_verdict_record
