"""Acceptance Scenario 8: legacy representation migration on restart.

Write a contradicting legacy checkpoint + integration record pair to
disk (checkpoint claims ``development``, the legacy record claims
``integrating`` with repository-global ``ls-files`` evidence), then
restart via the public parse/load paths. Migration must resolve to
exactly one authoritative state -- explicit
``RAISE_DIAGNOSTIC_INTERVENTION`` for the legacy global-evidence
record -- and never merge contradictory truths heuristically.
"""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import parse_record_payload, read_record, write_record
from ralph.pipeline.checkpoint import Checkpoint
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from tests.test_checkpoint_and_record_persistence_boundary import _InMemoryBackend

_VALID_PHASES = frozenset({"integrating", "integrated"})

#: Legacy record payload: phase claims an in-flight integration while the
#: checkpoint below claims development, and the diagnostic evidence carries
#: the legacy repository-global ``ls-files`` key which must raise explicit
#: intervention rather than being silently migrated.
_LEGACY_INTERVENTION_PAYLOAD: dict[str, object] = {
    "phase": "integrating",
    "target": "main",
    "pre_feature_sha": "a" * 40,
    "pre_target_sha": "b" * 40,
    "diagnostic_evidence": [
        ["ls-files", "some_tracked_files"],
    ],
}

#: Legacy record payload with legacy dictionary evidence keys, which must
#: migrate cleanly to the new key space without intervention.
_LEGACY_DICT_EVIDENCE_PAYLOAD: dict[str, object] = {
    "phase": "integrating",
    "target": "main",
    "pre_feature_sha": "a" * 40,
    "pre_target_sha": "b" * 40,
    "diagnostic_evidence": {
        "work": "unstaged diff",
        "controls": "controls data",
    },
}


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


def _write_contradicting_legacy_pair(tmp_path: Path, payload: dict[str, object]) -> None:
    """Persist a legacy checkpoint claiming ``development`` alongside a
    legacy integration record claiming ``integrating``."""
    state = PipelineState(phase="development")
    backend = _InMemoryBackend()
    checkpoint = Checkpoint(Path(".agent/checkpoint.json"))
    checkpoint.save(state)

    migrated = parse_record_payload(payload)
    assert migrated is not None, "legacy record payload must migrate to a typed record"
    write_record(tmp_path, migrated)
    return backend


def test_legacy_restart_intervention_record_is_single_authority(tmp_path: Path) -> None:
    """Contradicting legacy pair with global evidence resolves to explicit
    intervention; the record wins over the checkpoint's ``development``."""
    _write_contradicting_legacy_pair(tmp_path, _LEGACY_INTERVENTION_PAYLOAD)

    # Restart: reload checkpoint and record through public load paths.
    checkpoint = Checkpoint(Path(".agent/checkpoint.json"))
    reloaded = checkpoint.load()
    assert reloaded is not None
    assert reloaded.phase == "development"

    persisted = read_record(tmp_path)
    assert persisted is not None
    # (a) Migration lands on exactly one closed phase value -- no merged
    # hybrid truth between checkpoint and record.
    assert persisted.phase in _VALID_PHASES
    # The legacy repository-global evidence forces explicit intervention,
    # never a heuristic merge that would drop the fingerprint.
    assert persisted.diagnostic_intervention_required is True

    policy = _policy()
    state = PipelineState(phase=reloaded.phase, rebase=RebaseState())
    new_state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=persisted)

    # (b) The reducer is the single writer for last_decision: it equals the
    # decide_phase_transition output computed over the resulting state.
    decision = decide_phase_transition(
        new_state,
        policy,
        integration_record=persisted,
        rebase_state=new_state.rebase,
    )
    assert decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert new_state.rebase.last_decision == decision.value


def test_legacy_restart_clean_dict_evidence_is_single_authority(tmp_path: Path) -> None:
    """Contradicting legacy pair with dictionary evidence migrates cleanly;
    the record still blocks recovery as the single authority."""
    _write_contradicting_legacy_pair(tmp_path, _LEGACY_DICT_EVIDENCE_PAYLOAD)

    checkpoint = Checkpoint(Path(".agent/checkpoint.json"))
    reloaded = checkpoint.load()
    assert reloaded is not None

    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.phase in _VALID_PHASES
    assert not persisted.diagnostic_intervention_required
    evidence = dict(persisted.diagnostic_evidence)
    assert evidence.get("unstaged_work") == "unstaged diff"
    assert evidence.get("commit_controls") == "controls data"

    policy = _policy()
    state = PipelineState(phase=reloaded.phase, rebase=RebaseState())
    new_state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=persisted)

    decision = decide_phase_transition(
        new_state,
        policy,
        integration_record=persisted,
        rebase_state=new_state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY
    assert new_state.rebase.last_decision == decision.value


def test_policy_defaults_bundle_consistent(tmp_path: Path) -> None:
    """The default policy bundle loads and the decision authority stays pure
    over the migrated record regardless of pre-seeded last_decision."""
    bundle = load_policy(Path(__file__).parents[2] / "ralph" / "policy" / "defaults")
    assert bundle.pipeline is not None

    _write_contradicting_legacy_pair(tmp_path, _LEGACY_INTERVENTION_PAYLOAD)
    persisted = read_record(tmp_path)
    assert persisted is not None

    policy = bundle.pipeline
    poisoned = PipelineState(
        phase="development",
        rebase=RebaseState().model_copy(
            update={"last_decision": PhaseDecision.CONTINUE_PHASE.value}
        ),
    )
    new_state, _ = reduce(
        poisoned, PipelineEvent.AGENT_SUCCESS, policy, integration_record=persisted
    )
    decision = decide_phase_transition(
        new_state,
        policy,
        integration_record=persisted,
        rebase_state=new_state.rebase,
    )
    # A stale pre-seeded last_decision must not survive the restart: the
    # reducer recomputes from current authority outputs only.
    assert new_state.rebase.last_decision == decision.value
    assert decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED
