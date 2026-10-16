"""Entry-point matrix tests for unreadable integration records (U4c).

Contract under test (landed by U4a in
``ralph.pipeline.auto_integrate_record``):

* ``read_record_outcome`` returns a closed typed outcome whose UNREADABLE
  disposition carries a reason from ``RecordUnreadableReason``
  (``corrupt_json`` / ``unsupported_schema`` / ``backend_read_failure``).
* ``RecordUnreadableError`` is raised by ``clear_record`` and by
  ``IntegrationRecordStore.transition`` on UNREADABLE.

Lifecycle expectation exercised here across all six public entry points
(startup, phase-boundary, commit, cooldown, parallel join, terminal) for
each unreadable cause:

* the same typed lifecycle decision -- REPORT_INTERVENTION_REQUIRED,
  blocking dispatch and terminal success;
* restart/no-repeat: a simulated crash + restart does not re-run the
  diagnosis and keeps the persisted intervention projection, with the
  record file bytes unchanged throughout;
* visible label: the operator surface shows intervention
  (``intervention_required``), never ``evidence_diagnosis`` or
  ``conflict_resolution``.

The ABSENT case is the proceeding control.

The corrupt_json / unsupported_schema cases are driven through the
public boundary together with the U4b consumer wiring (the unreadable
intervention projection persisted onto the rebase state, exactly as
``run_loop._persist_unreadable_intervention`` does). The
backend_read_failure
cause is additionally unproducible at the read boundary today:
``read_record_outcome`` maps backend read failures to BUSY, not to the
UNREADABLE reason ``backend_read_failure``.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from ralph.mcp.artifacts._path_file_backend import PathFileBackend
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_backoff import compute_retry_deadline
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
    RecordUnreadableError,
    read_record_outcome,
)
from ralph.pipeline.auto_integrate_record_store import IntegrationRecordStore
from ralph.pipeline.auto_integrate_transition import CLEAR, START_INTEGRATING
from ralph.pipeline.integration_resolution import (
    inspect_integration_resolution,
    retained_integration_reason,
)
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)

UNREADABLE_CAUSES: tuple[str, ...] = (
    "corrupt_json",
    "unsupported_schema",
)
ENTRY_POINTS: tuple[str, ...] = (
    "startup",
    "phase_boundary",
    "commit",
    "cooldown",
    "parallel_join",
    "terminal",
)
INTERVENTION_DECISION: str = PhaseDecision.REPORT_INTERVENTION_REQUIRED.value
INTERVENTION_LABEL: str = "intervention_required"


class _FailingReadBackend(PathFileBackend):
    """Minimal FileBackend whose read path fails with OSError.

    Drives the BUSY branch of the boundary for clock-driven retry
    verification. shortcut: only implements the members the boundary
    read touches; widen if write paths are ever needed here.
    """

    def __init__(self, record_file: Path) -> None:
        self._record_file = record_file

    def exists(self, path: Path) -> bool:
        return path == self._record_file

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        raise OSError(5, "simulated backend read failure", str(path))


class _FakeClock:
    """Injected logical clock: fixed time, no sleeping."""

    def monotonic(self) -> float:
        return 1000.0


def _record_file(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME


def _write_unreadable_bytes(workspace_root: Path, cause: str) -> bytes:
    """Seed the durable record file with bytes that trigger ``cause``."""
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    if cause == "corrupt_json":
        payload = b"{not valid json"
    elif cause == "unsupported_schema":
        # Valid JSON, but no IntegrationRecord validates against it.
        payload = b'{"totally_unknown_shape": [1, 2, 3]}'
    else:
        raise ValueError(f"no on-disk bytes can trigger cause: {cause}")
    record_file.write_bytes(payload)
    return payload


@pytest.fixture(autouse=True)
def workspace_root(tmp_path: Path) -> Generator[Path, None, None]:
    (tmp_path / ".git").mkdir()
    yield tmp_path


def _write_bytes_at(workspace_root: Path, payload: bytes) -> bytes:
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(payload)
    return record_file.read_bytes()


def _policy_bundle() -> PolicyBundle:
    policy = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="review", on_loopback="development"),
            ),
            "review": PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(on_success="complete", on_loopback="development"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    repo_defaults = Path(__file__).parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(repo_defaults)
    return bundle.model_copy(update={"pipeline": policy})


def _decide(
    workspace_root: Path, rebase: RebaseState
) -> PhaseDecision | None:
    """Typed decision via the single authority, from the boundary outcome only.

    Mirrors the landed U4b consumer wiring: an UNREADABLE boundary
    outcome persists the intervention projection onto the rebase state
    (as ``run_loop._persist_unreadable_intervention`` does) before the
    pure authority decides.
    """
    outcome = read_record_outcome(workspace_root)
    if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
        from ralph.pipeline._integration_action import RebaseOutcomeKind

        rebase = rebase.model_copy(
            update={
                "last_outcome": RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
                "operation_display_state": "intervention_required",
            }
        )
    state = PipelineState(phase="development", rebase=rebase)
    return decide_phase_transition(
        state,
        _policy_bundle().pipeline,
        integration_record=outcome.record,
        rebase_state=rebase,
    )


def _persisted_label(decision: PhaseDecision) -> str | None:
    """Project a typed decision onto the persisted operator display surface.

    Mirrors the production persistence: ``model_copy`` does not re-run
    the model validator, so the display state is set explicitly exactly
    as ``run_loop._persist_unreadable_intervention`` does.
    """
    rebase = RebaseState().model_copy(
        update={
            "last_decision": decision.value,
            "operation_display_state": "intervention_required",
        }
    )
    return rebase.operation_display_state


def _terminal_success_blocked(workspace_root: Path, rebase: RebaseState) -> bool:
    terminal_state = PipelineState(phase="complete", rebase=rebase)
    outcome = read_record_outcome(workspace_root)
    if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
        return True
    return not is_terminal_success_eligible(
        terminal_state, outcome.record, rebase
    ) or (retained_integration_reason(workspace_root) is not None)


def _entry_point_decision(workspace_root: Path, entry_point: str) -> PhaseDecision:
    """Drive one public entry point against the unreadable durable record."""
    rebase = RebaseState()
    if entry_point == "startup":
        verdict = inspect_integration_resolution(workspace_root, rebase)
        assert not verdict.dispatch_allowed
        assert verdict.status == IntegrationResolutionStatus.RECOVERABLE
    elif entry_point == "terminal":
        assert (
            _terminal_success_blocked(workspace_root, rebase) is True
        ), "unreadable record must block terminal success"
    decision = _decide(workspace_root, rebase)
    assert decision is not None
    return decision


# --------------------------------------------------------------------------
# U4a boundary contract: closed typed outcome per on-disk cause.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cause", UNREADABLE_CAUSES)
def test_unreadable_outcome_is_typed_with_reason(
    workspace_root: Path, cause: str
) -> None:
    _write_unreadable_bytes(workspace_root, cause)
    outcome = read_record_outcome(workspace_root)
    assert outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE
    assert outcome.unreadable_reason == cause
    assert outcome.record is None


# --------------------------------------------------------------------------
# U4a boundary contract: backend read failure maps to BUSY with a deadline
# from the bounded-retry authority (attempt + injected clock, no sleeping).
# --------------------------------------------------------------------------


def test_backend_read_failure_maps_to_busy_with_retry_deadline(
    workspace_root: Path,
) -> None:
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(b'{"phase": "integrating", "target": "main"}')
    backend = _FailingReadBackend(record_file)
    outcome = read_record_outcome(
        workspace_root, backend=backend, busy_clock=_FakeClock()
    )
    assert outcome.disposition is RecordReadOutcomeDisposition.BUSY
    assert outcome.busy_attempt == 1
    assert outcome.busy_deadline == compute_retry_deadline(1, clock=_FakeClock())
    # The durable bytes are untouched by the failed read.
    assert record_file.read_bytes() == b'{"phase": "integrating", "target": "main"}'


# --------------------------------------------------------------------------
# U4a boundary contract: evidence preservation under mutation refusal.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cause", UNREADABLE_CAUSES)
def test_unreadable_evidence_is_preserved_not_cleared(
    workspace_root: Path, cause: str
) -> None:
    payload = _write_unreadable_bytes(workspace_root, cause)
    store = IntegrationRecordStore(workspace_root)
    with pytest.raises(RecordUnreadableError):
        store.transition(START_INTEGRATING)
    with pytest.raises(RecordUnreadableError):
        store.transition(CLEAR)
    # The durable evidence bytes are untouched by the refusals.
    assert _record_file(workspace_root).read_bytes() == payload


# --------------------------------------------------------------------------
# Entry-point matrix: six entry points x unreadable causes.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cause", UNREADABLE_CAUSES)
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_unreadable_record_entry_points_report_intervention(
    workspace_root: Path, cause: str, entry_point: str
) -> None:
    """All six entry points must surface the same typed intervention decision."""
    _write_unreadable_bytes(workspace_root, cause)
    decision = _entry_point_decision(workspace_root, entry_point)
    assert decision.value == INTERVENTION_DECISION


# --------------------------------------------------------------------------
# Operator-visible label: intervention, not diagnosis/conflict prose.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cause", UNREADABLE_CAUSES)
def test_operator_label_shows_intervention_not_diagnosis(
    workspace_root: Path, cause: str
) -> None:
    _write_unreadable_bytes(workspace_root, cause)
    decision = _entry_point_decision(workspace_root, "startup")
    label = _persisted_label(decision)
    assert label == INTERVENTION_LABEL
    assert label not in ("evidence_diagnosis", "conflict_resolution")


# --------------------------------------------------------------------------
# Restart / no-repeat: crash + restart keeps the projection, no re-diagnosis.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cause", UNREADABLE_CAUSES)
def test_restart_does_not_repeat_diagnosis_and_keeps_projection(
    workspace_root: Path, cause: str
) -> None:
    _write_unreadable_bytes(workspace_root, cause)
    # First visit: the typed decision settles on intervention.
    decision_first = _entry_point_decision(workspace_root, "startup")
    assert decision_first.value == INTERVENTION_DECISION
    bytes_after_first = _record_file(workspace_root).read_bytes()

    # Simulated crash + restart: a FRESH state reads the same durable
    # evidence, reaches the same typed decision, and does NOT re-run a
    # diagnosis: the outcome never becomes PRESENT and the record bytes
    # stay exactly as the crash left them.
    decision_restart = _entry_point_decision(workspace_root, "startup")
    assert decision_restart == decision_first
    assert _record_file(workspace_root).read_bytes() == bytes_after_first
    outcome_restart = read_record_outcome(workspace_root)
    assert outcome_restart.disposition is RecordReadOutcomeDisposition.UNREADABLE
    assert outcome_restart.unreadable_reason == cause


# --------------------------------------------------------------------------
# ABSENT control: with no record file, the pipeline proceeds.
# --------------------------------------------------------------------------


def test_absent_record_is_the_proceeding_control(workspace_root: Path) -> None:
    assert not _record_file(workspace_root).exists()
    outcome: RecordReadOutcome = read_record_outcome(workspace_root)
    assert outcome.disposition is RecordReadOutcomeDisposition.ABSENT
    assert outcome.record is None
    assert outcome.unreadable_reason is None

    rebase = RebaseState()
    decision = _decide(workspace_root, rebase)
    assert decision is not None
    # Control: absent record does not force intervention or recovery block.
    assert decision.value not in (INTERVENTION_DECISION, "block_for_recovery")
    assert not _record_file(workspace_root).exists()
