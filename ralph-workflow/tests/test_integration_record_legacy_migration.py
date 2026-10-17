"""One-way legacy-evidence and rebase outcome migration tests.

Acceptance scenario 8: legacy on-disk checkpoints carrying pre-fix diagnostic
evidence or string last_action fields migrate transparently through the public
loaders (read_record and RebaseState.model_validate).

The rewrite exercises only the public boundaries:
- Writes legacy on-disk JSON and reloads via public read_record
- Validates RebaseState via RebaseState.model_validate
- Asserts migrated last_outcome and the run-loop's typed PhaseDecision
- Covers the full round-trip for all 19 verbs plus None in RebaseOutcomeKind.from_legacy
  (including remote_failed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._pipeline_state_machine import (
    PhaseDecision,
    decide_phase_transition,
    is_terminal_success_eligible,
)
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    read_record,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy

ALL_19_LEGACY_VERBS: list[str] = [
    "rebased",
    "merged",
    "fast_forwarded",
    "skipped",
    "conflict",
    "recovered",
    "diagnosing",
    "evidence_diagnosis",
    "intervention_required",
    "diagnostic_intervention_required",
    "terminal",
    "completed",
    "remote_pushed",
    "remote_failed",
    "integrating",
    "landing",
    "publishing",
    "conflict_resolution",
    "waiting",
]


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


def test_public_checkpoint_reload_migrates_legacy_keys_and_routes_decision(
    tmp_path: Path,
) -> None:
    """Public read_record migrates legacy diagnostic keys and drives PhaseDecision."""
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)

    legacy_payload = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": str(tmp_path),
        "diagnostic_evidence": [
            ["work", "unstaged_content"],
            ["controls", "commit_ctrl"],
            ["/tmp/repo/.git/worktrees/wt/MERGE_HEAD", "merge_sha"],
            ["/tmp/repo/.git/logs/refs/heads/main", "dropped_reflog"],
        ],
    }
    record_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

    # Call public reload boundary
    loaded = read_record(tmp_path)
    assert loaded is not None
    assert loaded.phase == "integrating"

    keys = {k for k, _ in loaded.diagnostic_evidence}
    assert "unstaged_work" in keys
    assert "commit_controls" in keys
    assert "merge_state_files:/tmp/repo/.git/worktrees/wt/MERGE_HEAD" in keys
    # Common-dir reflog observation is dropped with marker
    assert "/tmp/repo/.git/logs/refs/heads/main" not in keys
    assert "legacy common-dir reflog observation dropped" in (loaded.repair_cap_reason or "")

    # Run-loop decision on migrated record
    policy = _policy()
    state = PipelineState(phase="development", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=loaded,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY


def test_public_checkpoint_reload_global_evidence_loads_cleanly(
    tmp_path: Path,
) -> None:
    """Legacy repository-global evidence (for-each-ref / ls-files) loads cleanly.

    The new diagnostic path drops the legacy global key silently
    and re-derives state from Git. Surfacing intervention here was
    the production livelock: the diagnostic could not re-verify
    and refused to proceed.
    """
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)

    legacy_payload = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": str(tmp_path),
        "diagnostic_evidence": [
            ["for-each-ref", "deadbeef"],
        ],
    }
    record_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = read_record(tmp_path)
    assert loaded is not None
    assert loaded.diagnostic_intervention_required is False
    # The dropped legacy key is no longer carried as live evidence.
    assert ("for-each-ref", "deadbeef") not in dict(loaded.diagnostic_evidence)


@pytest.mark.parametrize("verb", ALL_19_LEGACY_VERBS)
def test_rebase_state_model_validate_migrates_all_19_verbs(verb: str) -> None:
    """RebaseState.model_validate deserializes all 19 legacy last_action strings."""
    state = RebaseState.model_validate({"last_action": verb})
    expected_kind = RebaseOutcomeKind.from_legacy(verb)
    assert state.last_outcome == expected_kind
    assert state.last_action == verb


def test_rebase_state_model_validate_none() -> None:
    """RebaseState.model_validate with None last_action remains None."""
    state = RebaseState.model_validate({"last_action": None})
    assert state.last_outcome is None
    assert state.last_action is None


@pytest.mark.parametrize("verb", ALL_19_LEGACY_VERBS)
def test_rebase_outcome_kind_round_trip_all_19_verbs(verb: str) -> None:
    """Each of the 19 live legacy verbs round-trips through RebaseOutcomeKind."""
    kind = RebaseOutcomeKind.from_legacy(verb)
    assert kind is not None
    assert kind.value == verb
    assert RebaseOutcomeKind.from_legacy(kind.value) == kind


def test_rebase_outcome_kind_round_trip_none() -> None:
    """None maps cleanly to None."""
    assert RebaseOutcomeKind.from_legacy(None) is None


def test_rebase_outcome_kind_remote_failed_explicit() -> None:
    """remote_failed is specifically covered and projects through last_action."""
    kind = RebaseOutcomeKind.from_legacy("remote_failed")
    assert kind is RebaseOutcomeKind.REMOTE_FAILED
    state = RebaseState(last_outcome=kind)
    assert state.last_action == "remote_failed"


def test_rebase_state_model_validate_legacy_action_with_none_outcome() -> None:
    """When last_action is present and last_outcome is explicitly None, last_action is migrated."""
    state = RebaseState.model_validate({"last_action": "conflict", "last_outcome": None})
    assert state.last_outcome == RebaseOutcomeKind.CONFLICT
    assert state.last_action == "conflict"
    assert state.integration_unresolved is True

    policy = _policy()
    pipeline_state = PipelineState(phase="development", rebase=state)
    decision = decide_phase_transition(
        pipeline_state,
        policy,
        integration_record=None,
        rebase_state=state,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY
    assert is_terminal_success_eligible(pipeline_state, None, state) is False


def test_rebase_state_model_validate_unknown_legacy_action_blocks_dispatch() -> None:
    """Unknown legacy action produces UNKNOWN and blocks ordinary dispatch and terminal success."""
    state = RebaseState.model_validate({"last_action": "unrecognized_bogus"})
    assert state.last_outcome == RebaseOutcomeKind.UNKNOWN
    assert state.last_action == "unknown"
    assert state.integration_unresolved is True
    assert state.is_intervention_required is True

    policy = _policy()
    pipeline_state = PipelineState(phase="development", rebase=state)
    decision = decide_phase_transition(
        pipeline_state,
        policy,
        integration_record=None,
        rebase_state=state,
    )
    assert decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert is_terminal_success_eligible(pipeline_state, None, state) is False


@pytest.mark.parametrize(
    "payload",
    [
        {"last_action": "conflict", "last_outcome": "rebased"},
        {"last_action": "conflict", "last_outcome": RebaseOutcomeKind.REBASED},
        {"last_action": "rebased", "last_outcome": "conflict"},
        {"last_action": "rebased", "last_outcome": RebaseOutcomeKind.CONFLICT},
    ],
)
def test_rebase_state_model_validate_contradictory_values_blocks_dispatch(
    payload: dict[str, object],
) -> None:
    """Contradictory last_action and last_outcome produce UNKNOWN and block dispatch."""
    state = RebaseState.model_validate(payload)
    assert state.last_outcome == RebaseOutcomeKind.UNKNOWN
    assert state.last_action == "unknown"
    assert state.integration_unresolved is True
    assert state.is_intervention_required is True

    policy = _policy()
    pipeline_state = PipelineState(phase="development", rebase=state)
    decision = decide_phase_transition(
        pipeline_state,
        policy,
        integration_record=None,
        rebase_state=state,
    )
    assert decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert is_terminal_success_eligible(pipeline_state, None, state) is False
