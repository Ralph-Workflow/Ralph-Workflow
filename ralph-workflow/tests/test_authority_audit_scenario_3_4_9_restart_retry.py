"""Authority audit — acceptance scenarios 3, 4, and 9 (restart / retry / exhaustion).

Acceptance scenario 3: every named transition in the closed table (15 total,
including ``MARK_LEGACY_FIELDS_PRESERVED`` and ``CLEAR``) is interrupted both
BEFORE and AFTER the durable persistence inside
:meth:`IntegrationRecordStore.transition`. The authority/store is then
recreated as fresh restart objects, and the test asserts retained ownership,
the authoritative next action, and no replay of completed business work.

Acceptance scenario 4: make the durable record unreadable. Recovery and the
dispatch/mutation boundary stay blocked, intervention is surfaced, and the
unreadable bytes are retained untouched.

Acceptance scenario 9: recovery is exhausted through the real
``handoff_integration_diagnostic`` boundary, then the runtime objects
(store, collector, invoke probes) are freshly recreated and the handoff is
exercised again: attempts stay at the cap (no replenishment) and no renewed
diagnostic invocation happens after restart.

All tests run against real on-disk records via the default durable backend;
no subprocesses are spawned, so this is a plain (non-``subprocess_e2e``)
suite.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline._integration_action import (
    IntegrationAction,
    next_integration_action,
)
from ralph.pipeline._integration_diagnostic import (
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    handoff_integration_diagnostic,
)
from ralph.pipeline._pipeline_state_machine import (
    PhaseDecision,
    decide_phase_transition,
)
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    IntegrationRecord,
)
from ralph.pipeline.auto_integrate_record_store import (
    IntegrationRecordStore,
    RecordBusyError,
    RecordUnreadableError,
)
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.auto_integrate_transition import Transition, transition_by_name
from ralph.pipeline.operation_evidence import InMemoryOperationEvidenceCollector
from ralph.pipeline.operation_fact import OperationFact
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope
from tests._resolver_controller import make_resolver_controller

# --- Shared helpers -----------------------------------------------------


def _record_path(root: Path) -> Path:
    return root / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME


def _seed_record(root: Path, **extra: object) -> IntegrationRecord:
    """Seed a durable integrating record through a real store."""
    payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
    }
    payload.update(extra)
    record = IntegrationRecord.model_validate(payload)
    IntegrationRecordStore(root).write(record)
    return record


class _CrashBeforeWriteBackend:
    """Backend whose every persisting call crashes BEFORE bytes reach disk."""

    def __init__(self, inner: FileBackend) -> None:
        self._inner = inner

    def _crash(self, *_args: object) -> None:
        raise OSError("simulated crash before durable persistence")

    write_bytes = _crash
    write_text = _crash

    def replace(self, source: Path, destination: Path) -> None:
        self._crash(source, destination)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _CrashAfterWriteBackend:
    """Backend whose persists land durably and THEN crash.

    Only ``replace`` (the atomic publication step) is wrapped: the staged
    bytes are delegated to the real backend first, so the record is fully
    durable before the simulated crash fires.
    """

    def __init__(self, inner: FileBackend) -> None:
        self._inner = inner

    def replace(self, source: Path, destination: Path) -> None:
        self._inner.replace(source, destination)
        raise OSError("simulated crash after durable persistence")

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


# --- Scenario 3: crash at each lifecycle transition, then restart -------


_MATRIX: tuple[tuple[str, dict[str, object]], ...] = (
    (
        "START_INTEGRATING",
        {"target": "main", "pre_feature_sha": "a" * 40, "pre_target_sha": "b" * 40},
    ),
    ("PROMOTE_TO_INTEGRATED", {"integrated_feature_sha": "c" * 40}),
    (
        "MARK_RESOLVING_REBASE",
        {"resolving_rebase": True, "resolving_merge": False, "pre_target_sha": "b" * 40},
    ),
    (
        "MARK_PENDING_REBASE_CONTINUE",
        {"rebase_continue_pending": True, "rebase_continue_head": "h" * 40},
    ),
    (
        "MARK_RESOLVING_MERGE",
        {"resolving_merge": True, "resolving_paths": ("path",), "resolving_stop_sha": "s" * 40},
    ),
    (
        "MARK_PENDING_MERGE_COMMIT",
        {"merge_commit_pending": True, "merge_commit_head": "h" * 40},
    ),
    ("ACCEPT_REPAIR_EDITS", {"repair_pending_diff": "diff", "repair_pending_paths": ()}),
    ("RESUME_REPAIR", {"repair_pending_diff": "diff", "repair_pending_paths": ()}),
    (
        "HOLD_DIAGNOSTIC_EVIDENCE",
        # ``head`` is a valid collector-evidence key produced by the
        # current collector (see
        # ``_auto_integrate_record_restamp._VALID_COLLECTOR_EVIDENCE_EXACT_KEYS``).
        # The placeholder ``(k, v)`` previously used here was a
        # non-collector key and was dropped by the migration loader
        # under the AC1 / O1 contract; ``head`` lets this transition
        # survive a crash-after round-trip intact.
        {
            "diagnostic_evidence": (("head", "v"),),
            "diagnostic_ownership": "owner",
        },
    ),
    ("RELEASE_DIAGNOSTIC_EVIDENCE", {"diagnostic_evidence": (), "diagnostic_ownership": None}),
    ("BUMP_DIAGNOSTIC_ATTEMPT", {"merge_commit_repair_attempts": 1, "repair_last_error": "x"}),
    (
        "RAISE_DIAGNOSTIC_INTERVENTION",
        {"diagnostic_intervention_required": True, "repair_cap_reason": "cap"},
    ),
    ("MARK_REINTEGRATE_PENDING", {"reintegrate_pending": True, "owning_worktree": "/tmp/owner"}),
    (
        "MARK_LEGACY_FIELDS_PRESERVED",
        {
            "diagnostic_evidence": (("head", "v"),),
            "diagnostic_ownership": "owner",
            "repair_cap_reason": "legacy preserved",
        },
    ),
    ("CLEAR", {}),
)

# The matrix must cover exactly the closed transition table.
assert {t.name for t in Transition.ALL} == {name for name, _ in _MATRIX}


@pytest.mark.parametrize(
    ("transition_name", "kwargs"),
    _MATRIX,
    ids=[f"{name}-before" for name, _ in _MATRIX],
)
def test_crash_before_persistence_retains_prior_ownership_on_restart(
    tmp_path: Path, transition_name: str, kwargs: dict[str, object]
) -> None:
    """Crash BEFORE the durable write: the prior state owns the workspace after restart."""
    seeded = _seed_record(tmp_path)
    original_bytes = _record_path(tmp_path).read_bytes()
    transition = transition_by_name(transition_name)

    crashing_store = IntegrationRecordStore(
        tmp_path, backend=_CrashBeforeWriteBackend(DEFAULT_FILE_BACKEND)
    )
    with contextlib.suppress(OSError):
        crashing_store.transition(transition, **kwargs)
    # A byte-identical update legitimately skips publication entirely
    # (atomic_write_bytes_if_changed), so "no write, no crash" is the
    # correct crash-before outcome for idempotent replays too.

    # Restart: fresh authority + fresh store, real backend.
    restarted_store = IntegrationRecordStore(tmp_path)
    reloaded = restarted_store.read()
    assert reloaded == seeded, "crash-before must leave the prior recoverable state"
    assert _record_path(tmp_path).read_bytes() == original_bytes

    # The authoritative next action is unchanged by the crash.
    assert next_integration_action(reloaded) == next_integration_action(seeded)


@pytest.mark.parametrize(
    ("transition_name", "kwargs"),
    _MATRIX,
    ids=[f"{name}-after" for name, _ in _MATRIX],
)
def test_crash_after_persistence_is_recovered_and_never_replays_business_work(
    tmp_path: Path, transition_name: str, kwargs: dict[str, object]
) -> None:
    """Crash AFTER the durable write: the new state owns the workspace after restart.

    Restarting must observe the transitioned state, and replaying the same
    transition through a fresh store must NOT replay completed business
    work (durable identity fields stay fixed; the replay is either
    idempotent or refused by the transition guard).
    """
    seeded = _seed_record(tmp_path)
    transition = transition_by_name(transition_name)
    expected = seeded.with_transition(transition, **kwargs)

    crashing_store = IntegrationRecordStore(
        tmp_path, backend=_CrashAfterWriteBackend(DEFAULT_FILE_BACKEND)
    )
    # The crash fires after the bytes are already durable.
    with contextlib.suppress(OSError):
        crashing_store.transition(transition, **kwargs)

    # Restart: fresh authority + fresh store, real backend.
    restarted_store = IntegrationRecordStore(tmp_path)
    reloaded = restarted_store.read()
    assert reloaded is not None
    if transition_name == "RAISE_DIAGNOSTIC_INTERVENTION":
        # Legacy human-hand-off transition: load-and-clear clears the flag on restart.
        expected_dump = expected.model_copy(
            update={"diagnostic_intervention_required": False, "diagnostic_evidence": ()}
        ).model_dump()
        assert reloaded.model_dump() == expected_dump
    else:
        assert reloaded.model_dump() == expected.model_dump(), (
            "crash-after must leave the transitioned recoverable state"
        )
        assert next_integration_action(reloaded) == next_integration_action(expected)

    # No replay of completed business work: the durable identity fields
    # never move, no matter how the replay attempt terminates.
    identity_before = (
        reloaded.phase,
        reloaded.target,
        reloaded.pre_feature_sha,
        reloaded.pre_target_sha,
    )
    # Guard refusal is a valid no-replay outcome.
    with contextlib.suppress(ValueError, RecordBusyError, RecordUnreadableError):
        restarted_store.transition(transition, **kwargs)
    after_replay = restarted_store.read()
    assert after_replay is not None
    identity_after = (
        after_replay.phase,
        after_replay.target,
        after_replay.pre_feature_sha,
        after_replay.pre_target_sha,
    )
    assert identity_after == identity_before


# --- Scenario 4: unreadable evidence blocks dispatch and is retained ----


def test_unreadable_evidence_blocks_dispatch_and_recovery_retains_bytes(
    tmp_path: Path,
) -> None:
    """UNREADABLE record: recovery reports intervention, mutations are refused,
    bytes are retained untouched."""
    record_file = _record_path(tmp_path)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text("{not valid json", encoding="utf-8")
    original_bytes = record_file.read_bytes()

    # Startup recovery maps the unreadable evidence to typed ladder escalation.
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_path))
    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.LADDER_ESCALATION
    assert outcome.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value
    assert outcome.recovery_record_retained is True
    assert record_file.read_bytes() == original_bytes

    # The pipeline state machine surfaces BLOCK_FOR_RECOVERY.
    state = PipelineState(phase="development", rebase=outcome)
    decision = decide_phase_transition(state, None, integration_record=None, rebase_state=outcome)
    assert decision is PhaseDecision.BLOCK_FOR_RECOVERY
    assert record_file.read_bytes() == original_bytes

    # The dispatch/mutation boundary is blocked: every durable mutation is
    # refused with a typed error through a fresh store (restart), and the
    # unreadable bytes remain the retained ownership evidence.
    fresh_store = IntegrationRecordStore(tmp_path)
    with pytest.raises(RecordUnreadableError):
        fresh_store.transition(transition_by_name("START_INTEGRATING"), target="main")
    assert record_file.read_bytes() == original_bytes


# --- Scenario 9: actual recovery exhaustion across fresh restarts -------


def _handoff_record(attempts: int) -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        resolving_rebase=True,
        merge_commit_repair_attempts=attempts,
    )


def test_exhaustion_across_restarts_no_replenishment_and_no_renewed_invocation(
    tmp_path: Path,
) -> None:
    """Scenario 9: exhaust recovery through the real handoff boundary, then
    restart with freshly created runtime objects and exercise it again.
    Attempts stay at the cap (no replenishment) and no renewed diagnostic
    invocation happens after restart."""
    cap = DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    invoked: list[str] = []

    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return False

    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )

    # --- First runtime: exhaust recovery (cap-1 -> cap) through the ---
    # --- real handoff boundary --------------------------------------
    record = _handoff_record(cap - 1)
    assert not handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=tmp_path,
        record=record,
        failure="receipt unreadable",
        agents=(),  # no eligible agents: the handoff consumes one attempt
        invoke=invoke,
        collector=collector,
    )
    record = IntegrationRecord.model_validate_json(_record_path(tmp_path).read_bytes())
    assert record.merge_commit_repair_attempts == cap
    assert record.diagnostic_intervention_required is False
    assert "diagnostic repair budget spent" in (record.repair_cap_reason or "")
    assert invoked == []

    # --- Restart: fresh authority over the same durable record --------
    rebase_state = RebaseState(last_outcome=RebaseOutcomeKind.LADDER_ESCALATION)
    state = PipelineState(phase="development", rebase=rebase_state)
    assert (
        decide_phase_transition(state, None, integration_record=None, rebase_state=rebase_state)
        is PhaseDecision.BLOCK_FOR_RECOVERY
    )

    # --- Second runtime: freshly created runtime objects; restart must ---
    # --- neither replenish the budget nor re-invoke a diagnostic agent ---
    fresh_collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )
    fresh_invoked: list[str] = []

    def fresh_invoke(agent: str, _prompt: Path) -> bool:
        fresh_invoked.append(agent)
        return False

    for _round in range(2):
        assert not handoff_integration_diagnostic(
            controller=make_resolver_controller(),
            root=tmp_path,
            record=record,
            failure="receipt unreadable after restart",
            agents=("agent-a", "agent-b"),
            invoke=fresh_invoke,
            collector=fresh_collector,
        )
        reloaded = IntegrationRecord.model_validate_json(_record_path(tmp_path).read_bytes())
        assert reloaded.merge_commit_repair_attempts == cap, (
            "restart must never replenish the attempt budget"
        )
        assert reloaded.diagnostic_intervention_required is False
        assert "diagnostic repair budget spent" in (reloaded.repair_cap_reason or "")
        assert fresh_invoked == [], "no renewed diagnostic invocation after restart"
        record = reloaded


def test_kill_at_cap_does_not_replenish_after_restart(tmp_path: Path) -> None:
    """Scenario 9 (kill-at-cap): a deliberate SIGKILL at attempt == cap.

    The first post-restart handoff must emit
    ``RAISE_DIAGNOSTIC_INTERVENTION`` (no new agent invocation, no
    budget replenishment). The runtime is freshly created, exercising
    the actual restart path.
    """
    cap = DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    # Seed a record already at the cap with a retained intervention
    # flag, then SIGKILL (simulated by simply reloading from disk and
    # constructing a fresh runtime) and re-enter the handoff.
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        resolving_rebase=True,
        merge_commit_repair_attempts=cap,
        diagnostic_intervention_required=True,
        repair_cap_reason="diagnostic repair exhausted (kill-at-cap fixture)",
    )
    IntegrationRecordStore(tmp_path).write(record)

    invoked: list[str] = []
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )

    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return False

    # Restart = freshly created runtime over the same durable record.
    reloaded = IntegrationRecord.model_validate_json(_record_path(tmp_path).read_bytes())
    assert reloaded.merge_commit_repair_attempts == cap
    assert reloaded.diagnostic_intervention_required is True

    # The first post-restart handoff must NOT replenish the budget and
    # must NOT invoke a diagnostic agent. The action authority over
    # the reloaded record is REQUIRE_INTERVENTION, so
    # ``decide_phase_transition`` must report the intervention.
    assert next_integration_action(reloaded) is IntegrationAction.REQUIRE_INTERVENTION
    rebase_state = RebaseState(last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED)
    state = PipelineState(phase="development", rebase=rebase_state)
    assert (
        decide_phase_transition(state, None, integration_record=None, rebase_state=rebase_state)
        is PhaseDecision.REPORT_INTERVENTION_REQUIRED
    )

    # The handoff itself refuses: the existing cap is honoured, no
    # renewed invocation, no budget mutation.
    assert not handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=tmp_path,
        record=reloaded,
        failure="receipt unreadable after kill-at-cap",
        agents=("agent-a", "agent-b"),
        invoke=invoke,
        collector=collector,
    )
    after = IntegrationRecord.model_validate_json(_record_path(tmp_path).read_bytes())
    assert after.merge_commit_repair_attempts == cap, (
        "kill-at-cap must never replenish the attempt budget"
    )
    assert after.diagnostic_intervention_required is True
    assert invoked == [], "no renewed diagnostic invocation after kill-at-cap"

    # Restart again: the persisted cap is the same; the type system
    # refuses both ordinary dispatch and a fresh diagnostic.
    reloaded_again = IntegrationRecord.model_validate_json(_record_path(tmp_path).read_bytes())
    assert reloaded_again.merge_commit_repair_attempts == cap
    assert next_integration_action(reloaded_again) is IntegrationAction.REQUIRE_INTERVENTION
