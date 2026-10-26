"""Real public-boundary entry-point tests for unreadable integration records.

Every entry point (startup, phase boundary, commit, cooldown, parallel
join, terminal) is driven through its REAL production seam against a
real git worktree whose durable record file carries unreadable bytes:

* startup: ``run_loop._run_auto_integrate_recovery_preamble``
* phase boundary: ``run_loop._run_inner_loop`` with an injected sleep
* commit: ``prepare_commit_integration`` + ``runner._maybe_auto_integrate``
* cooldown: ``run_loop._wait_for_integration_retry`` (injected sleep)
* parallel join: ``run_worker_auto_integration(recover_first=True)``
* terminal: ``decide_phase_transition`` at the terminal phase plus
  ``is_terminal_success_eligible``

Across every entry point and every unreadable cause (``corrupt_json``,
``unsupported_schema``, and a real ``PermissionError`` backend read
failure) the same typed lifecycle decision must hold: the unreadable
record escalates into the AUTONOMOUS FALLBACK LADDER (the typed
``LADDER_ESCALATION`` outcome), dispatch and terminal success stay
blocked, a restart does NOT re-run the diagnosis agent, the checkpoint
reload keeps the escalation projection, and the visible label names the
next autonomous action -- never a terminal intervention state. The
record file bytes are preserved untouched throughout.

The ``PermissionError`` backend read failure is driven through the
entry points that can THREAD the failing backend into the production
seam -- startup recovery, the phase-boundary loop, the cooldown
executor and the terminal authority. The commit and parallel-join
seams have no backend seam to thread, so for that cause they reach the
SAME startup recovery producer and are covered by the startup case.

The ABSENT case is the proceeding control.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.auto_integrate_message import format_integration_label
from ralph.mcp.artifacts._path_file_backend import PathFileBackend
from ralph.pipeline import checkpoint as checkpoint_module
from ralph.pipeline import run_loop, runner
from ralph.pipeline._pipeline_state_machine import (
    PhaseDecision,
    decide_phase_transition,
)
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    RecordReadOutcomeDisposition,
    RecordUnreadableError,
    read_record_outcome,
)
from ralph.pipeline.auto_integrate_record_store import IntegrationRecordStore
from ralph.pipeline.auto_integrate_transition import CLEAR, START_INTEGRATING
from ralph.pipeline.effects.commit_effect import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_resolution import (
    inspect_integration_resolution,
    retained_integration_reason,
)
from ralph.pipeline.parallel.worker_runtime import run_worker_auto_integration
from ralph.pipeline.plumbing.commit_integration import prepare_commit_integration
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]

UNREADABLE_CAUSES: tuple[str, ...] = ("corrupt_json", "unsupported_schema")
#: The typed autonomous escalation: the unreadable durable record blocks
#: for recovery (the fallback ladder), never on a person.
ESCALATION = PhaseDecision.BLOCK_FOR_RECOVERY
ESCALATION_OUTCOME = "ladder_escalation"
#: The visible label names the next autonomous action (the ladder), so a
#: terminal intervention label must never appear.
FORBIDDEN_LABELS = ("intervention",)

# The inner loop's cooldown executor sleeps once per blocked turn; this
# sentinel ends the loop deterministically on the first injected sleep.
_INNER_LOOP_STOP = StopIteration("inner loop stopped at the first cooldown sleep")


class _FailingReadBackend(PathFileBackend):
    """FileBackend whose read path fails with a PermissionError.

    Drives the UNREADABLE(backend_read_failure) branch of the public
    read boundary.
    """

    def __init__(self, record_file: Path) -> None:
        self._record_file = record_file

    def exists(self, path: Path) -> bool:
        return path == self._record_file

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        raise PermissionError(13, "Permission denied", str(path))


def _record_file(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME


def _seed_unreadable_record(workspace_root: Path, cause: str) -> bytes:
    """Seed the durable record file with bytes that trigger ``cause``."""
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    if cause == "corrupt_json":
        payload = b"{not valid json"
    elif cause == "unsupported_schema":
        payload = b'{"totally_unknown_shape": [1, 2, 3]}'
    else:
        raise ValueError(f"no on-disk bytes can trigger cause: {cause}")
    record_file.write_bytes(payload)
    return payload


@pytest.fixture(autouse=True)
def workspace_root(tmp_path: Path) -> Generator[Path, None, None]:
    """A real git worktree: the entry-point seams run real git commands."""
    from ralph.git.subprocess_runner import GitRunOptions, run_git

    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "test"],
    ):
        assert (
            run_git(
                args,
                cwd=tmp_path,
                label="unreadable-record-test",
                options=GitRunOptions(timeout=5),
            ).returncode
            == 0
        )
    yield tmp_path


@pytest.fixture(autouse=True)
def no_diagnosis_agent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count diagnostic-agent invocations; the unreadable handoff must make none."""
    import ralph.pipeline._integration_diagnostic as diagnostic_module

    calls: list[str] = []

    def _forbidden_diagnose(ctx: object, failure: str) -> bool:
        calls.append(failure)
        raise AssertionError(
            f"diagnosis agent must not run for an unreadable record; invoked with: {failure}"
        )

    monkeypatch.setattr(diagnostic_module, "diagnose_retained_integration", _forbidden_diagnose)
    return calls


def _as_mock(value: object) -> MagicMock:
    """Narrow ``object`` to ``MagicMock`` for tests that need to call mock methods.

    The helper exists so the test module never uses ``typing.cast`` over
    arbitrary test data; the type checker is told the truth via the
    runtime ``isinstance`` check rather than an assertion. The call site
    is bounded: this function is only used to hand a ``MagicMock`` (the
    pre-existing contract for ``run_loop._LoopContext.sleep``) into a
    parameter typed as ``object``.
    """
    assert isinstance(value, MagicMock), f"expected MagicMock, got {type(value).__name__}"
    return value


def _policy_bundle() -> PolicyBundle:
    policy = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="commit",
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
    return load_policy(repo_defaults).model_copy(update={"pipeline": policy})


def _loop_context(
    workspace_root: Path,
    latest_state: list[PipelineState],
    sleep: Callable[[float], None],
) -> run_loop._LoopContext:
    return run_loop._LoopContext(
        policy_bundle=_policy_bundle(),
        workspace_scope=WorkspaceScope(workspace_root),
        config=UnifiedConfig(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=MagicMock(),
        registry={},
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=MagicMock(),
        sleep=_as_mock(sleep),
        is_quiet=True,
        latest_state=latest_state,
    )


def _authority_decision(rebase: RebaseState, *, phase: str = "development") -> PhaseDecision:
    return decide_phase_transition(
        PipelineState(phase=phase, rebase=rebase),
        _policy_bundle().pipeline,
        integration_record=None,
        rebase_state=rebase,
    )


def _assert_escalation(
    workspace_root: Path,
    rebase: RebaseState,
    payload: bytes,
    *,
    phase: str = "development",
) -> PhaseDecision:
    """The single authority must block for the ladder; evidence stays intact."""
    decision = _authority_decision(rebase, phase=phase)
    assert decision is ESCALATION
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED
    # The escalation flag marks the escalation as owning the state (the
    # run loop's escalation machinery owns it from there); the durable
    # outcome is the typed ladder escalation, never the retired
    # intervention outcome.
    assert rebase.is_intervention_required is True
    assert rebase.last_outcome is not None
    assert rebase.last_outcome.value == ESCALATION_OUTCOME
    label = format_integration_label(rebase)
    assert label not in FORBIDDEN_LABELS
    assert _record_file(workspace_root).read_bytes() == payload
    assert not inspect_integration_resolution(workspace_root, rebase).dispatch_allowed
    assert retained_integration_reason(workspace_root) is not None
    return decision


# --------------------------------------------------------------------------
# U4a boundary contract: closed typed outcome per on-disk cause.
# --------------------------------------------------------------------------


def test_unreadable_outcome_is_typed_with_reason(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        outcome = read_record_outcome(workspace_root)
        assert outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE
        assert outcome.unreadable_reason == cause
        assert outcome.record is None
        assert _record_file(workspace_root).read_bytes() == payload


# --------------------------------------------------------------------------
# Backend read failure maps to UNREADABLE(backend_read_failure); the
# durable bytes are untouched by the failed read.
# --------------------------------------------------------------------------


def test_backend_read_failure_maps_to_unreadable_backend_read_failure(
    workspace_root: Path,
) -> None:
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(b'{"phase": "integrating", "target": "main"}')
    outcome = read_record_outcome(workspace_root, backend=_FailingReadBackend(record_file))
    assert outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE
    assert outcome.unreadable_reason == "backend_read_failure"
    assert outcome.record is None
    assert outcome.busy_attempt is None
    assert outcome.busy_deadline is None
    assert record_file.read_bytes() == b'{"phase": "integrating", "target": "main"}'


# --------------------------------------------------------------------------
# U4a boundary contract: evidence preservation under mutation refusal.
# --------------------------------------------------------------------------


def test_unreadable_evidence_is_preserved_not_cleared(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        store = IntegrationRecordStore(workspace_root)
        with pytest.raises(RecordUnreadableError):
            store.transition(START_INTEGRATING)
        with pytest.raises(RecordUnreadableError):
            store.transition(CLEAR)
        assert _record_file(workspace_root).read_bytes() == payload


# --------------------------------------------------------------------------
# REAL entry point: startup (crash-recovery preamble + dispatch gate).
# --------------------------------------------------------------------------


def test_startup_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), UnifiedConfig()
        )
        assert recovered is not None
        assert recovered.last_outcome.value == ESCALATION_OUTCOME
        assert recovered.last_decision == ESCALATION.value
        _assert_escalation(workspace_root, recovered, payload)


# --------------------------------------------------------------------------
# REAL entry point: phase boundary (full inner loop; injects the sleep).
# --------------------------------------------------------------------------


def test_phase_boundary_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), UnifiedConfig()
        )
        assert recovered is not None
        state = PipelineState(phase="development", rebase=recovered)
        latest_state: list[PipelineState] = []
        sleep = MagicMock(side_effect=_INNER_LOOP_STOP)
        ctx = _loop_context(workspace_root, latest_state, sleep)
        # O1 retry-progress rule (D3) + WU-1: the phase-boundary loop
        # escalates an unreadable durable record to the typed ladder
        # outcome WITHOUT the fixed-interval cooldown sleep masking the
        # escalation -- the FIRST escalation never sleeps. The loop also
        # never returns early on a person: repeat escalations pace the
        # loop with the bounded retry (the sentinel ends the loop
        # deterministically at the first paced sleep).
        with contextlib.suppress(StopIteration):
            run_loop._run_inner_loop(state, ctx, "development")
        assert sleep.called, (
            "repeat escalations must pace the loop with the bounded retry "
            "instead of returning early on a person"
        )
        assert all(call.args[0] > 0 for call in sleep.call_args_list)
        result_state = latest_state[0]
        assert result_state.rebase.last_decision == ESCALATION.value
        _assert_escalation(workspace_root, result_state.rebase, payload)
        # The blocked turn checkpoints through the real persistence boundary.
        reloaded = checkpoint_module.load(workspace_root / "checkpoint.json")
        assert reloaded is not None
        assert reloaded.rebase.last_decision == ESCALATION.value


# --------------------------------------------------------------------------
# REAL entry point: commit (commit-integration plumbing + commit seam).
# --------------------------------------------------------------------------


def test_commit_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        deps = MagicMock()
        deps.policy_bundle = _policy_bundle()
        deps.policy_bundle_factory = None
        verdict = prepare_commit_integration(
            workspace_root,
            UnifiedConfig(),
            deps,
            MagicMock(),
            MagicMock(),
            conflict_resolver=MagicMock(),
            rebase_stop_resolver=MagicMock(),
        )
        assert not verdict.dispatch_allowed
        bundle = _policy_bundle()
        phase_def = bundle.pipeline.phases["development"]
        outcome = runner._maybe_auto_integrate(
            effect=CommitEffect(message_file="msg.txt"),
            event=PipelineEvent.COMMIT_SUCCESS,
            commit_phase_def=phase_def,
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(workspace_root),
            state=PipelineState(phase="development", rebase=RebaseState()),
            display=MagicMock(),
            policy_bundle=bundle,
            registry=MagicMock(),
        )
        assert outcome is not None
        assert outcome.last_outcome.value == ESCALATION_OUTCOME
        _assert_escalation(workspace_root, outcome, payload)


# --------------------------------------------------------------------------
# REAL entry point: cooldown (the loop's cooldown executor; injected sleep).
# --------------------------------------------------------------------------


def test_cooldown_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), UnifiedConfig()
        )
        assert recovered is not None
        state = PipelineState(phase="development", rebase=recovered)
        latest_state: list[PipelineState] = [state]
        sleep = MagicMock()
        ctx = _loop_context(workspace_root, latest_state, sleep)
        # O1 retry-progress rule (D3): the cooldown retry path escalates
        # an unreadable durable record to INTERVENTION_REQUIRED WITHOUT
        # calling the fixed-interval cooldown sleep. The unreadable
        # record has no in-process satisfier (no lease, no evidence);
        # the 5-second sleep that lived here was the operator-only stall
        # that D3 forbade. Escalation must be immediate.
        cooled = run_loop._wait_for_integration_retry(state, ctx)
        assert not sleep.called, (
            "the cooldown retry path must escalate an unreadable record "
            "without calling the cooldown sleep (D3)"
        )
        assert cooled.rebase.last_decision == ESCALATION.value
        _assert_escalation(workspace_root, cooled.rebase, payload)
        reloaded = checkpoint_module.load(workspace_root / "checkpoint.json")
        assert reloaded is not None
        assert reloaded.rebase.last_decision == ESCALATION.value


# --------------------------------------------------------------------------
# REAL entry point: parallel join (worker seam with recover_first=True).
# --------------------------------------------------------------------------


def test_parallel_join_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        joined = run_worker_auto_integration(
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(workspace_root),
            policy_bundle=_policy_bundle(),
            registry=MagicMock(),
            pipeline_deps=None,
            display_context=MagicMock(),
            recover_first=True,
            state=RebaseState(),
        )
        assert joined is not None
        assert joined.last_outcome.value == ESCALATION_OUTCOME
        assert joined.last_decision == ESCALATION.value
        _assert_escalation(workspace_root, joined, payload)


# --------------------------------------------------------------------------
# REAL entry point: terminal (terminal-phase authority + success gate).
# --------------------------------------------------------------------------


def test_terminal_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), UnifiedConfig()
        )
        assert recovered is not None
        terminal_state = PipelineState(phase="complete", rebase=recovered)
        assert is_terminal_success_eligible(terminal_state, None, recovered) is False
        decision = _authority_decision(recovered, phase="complete")
        assert decision is ESCALATION
        _assert_escalation(workspace_root, recovered, payload, phase="complete")


# --------------------------------------------------------------------------
# Backend read failure driven through the startup entry point seam.
# --------------------------------------------------------------------------


def test_startup_entry_point_escalates_to_the_ladder_on_backend_read_failure(
    workspace_root: Path,
) -> None:
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(b'{"phase": "integrating", "target": "main"}')
    # Drive the startup recovery seam with the failing backend: the
    # PermissionError from exists() maps to UNREADABLE(backend_read_failure)
    # at the producer, so the decision must be strictly REPORT_INTERVENTION_REQUIRED.
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration

    recovered = recover_incomplete_integration(
        WorkspaceScope(workspace_root),
        backend=_FailingReadBackend(record_file),
    )
    assert recovered is not None
    assert recovered.last_outcome.value == ESCALATION_OUTCOME
    assert recovered.last_decision == ESCALATION.value
    _assert_escalation(workspace_root, recovered, b'{"phase": "integrating", "target": "main"}')


# --------------------------------------------------------------------------
# Backend read failure driven through the phase-boundary entry point seam.
# --------------------------------------------------------------------------


def test_phase_boundary_entry_point_escalates_to_the_ladder_on_backend_read_failure(
    workspace_root: Path,
) -> None:
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(b'{"phase": "integrating", "target": "main"}')
    # Drive the phase-boundary loop with the failing backend threaded
    # through _run_inner_loop -> _apply_startup_rebase_outcomes ->
    # _run_auto_integrate_recovery_preamble -> recover_incomplete_integration.
    # The failing backend causes exists() to return True but read_text() to
    # raise PermissionError, mapping to UNREADABLE(backend_read_failure) and
    # strictly REPORT_INTERVENTION_REQUIRED.
    state = PipelineState(phase="development", rebase=RebaseState())
    latest_state: list[PipelineState] = []
    sleep = MagicMock(side_effect=_INNER_LOOP_STOP)
    ctx = _loop_context(workspace_root, latest_state, sleep)
    # O1 retry-progress rule (D3) + WU-1: same as the corrupt-in-parsed
    # case -- the failing backend has no in-process satisfier, so the
    # FIRST escalation is immediate (never the fixed-interval cooldown
    # sleep), and the loop never returns early on a person: repeat
    # escalations pace the loop with the bounded retry (the sentinel
    # ends the loop deterministically at the first paced sleep).
    with contextlib.suppress(StopIteration):
        run_loop._run_inner_loop(
            state,
            ctx,
            "development",
            backend=_FailingReadBackend(record_file),
        )
    assert sleep.called, (
        "repeat escalations must pace the loop with the bounded retry "
        "instead of returning early on a person"
    )
    blocked = latest_state[0]
    assert blocked.rebase.last_decision == ESCALATION.value
    _assert_escalation(
        workspace_root, blocked.rebase, b'{"phase": "integrating", "target": "main"}'
    )
    reloaded = checkpoint_module.load(workspace_root / "checkpoint.json")
    assert reloaded is not None
    assert reloaded.rebase.last_decision == ESCALATION.value


# --------------------------------------------------------------------------
# Backend read failure driven through the cooldown entry point seam.
# --------------------------------------------------------------------------


def test_cooldown_entry_point_escalates_to_the_ladder_on_backend_read_failure(
    workspace_root: Path,
) -> None:
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(b'{"phase": "integrating", "target": "main"}')
    # Drive the cooldown path with the failing backend threaded through
    # _wait_for_integration_retry -> _run_auto_integrate_recovery_preamble ->
    # recover_incomplete_integration.  The PermissionError from read_text()
    # maps to UNREADABLE(backend_read_failure) and strictly
    # REPORT_INTERVENTION_REQUIRED.
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration

    recovered = recover_incomplete_integration(
        WorkspaceScope(workspace_root),
        backend=_FailingReadBackend(record_file),
    )
    assert recovered is not None
    assert recovered.last_outcome.value == ESCALATION_OUTCOME
    assert recovered.last_decision == ESCALATION.value
    state = PipelineState(phase="development", rebase=recovered)
    latest_state: list[PipelineState] = [state]
    sleep = MagicMock()
    ctx = _loop_context(workspace_root, latest_state, sleep)
    cooled = run_loop._wait_for_integration_retry(
        state,
        ctx,
        backend=_FailingReadBackend(record_file),
    )
    # O1 retry-progress rule (D3): the failing backend has no in-process
    # satisfier, so the cooldown retry path escalates to
    # INTERVENTION_REQUIRED WITHOUT calling the cooldown sleep.
    assert not sleep.called, (
        "the cooldown retry path must escalate a backend read failure "
        "without calling the cooldown sleep (D3)"
    )
    cooled = ctx.latest_state[0]
    assert cooled.rebase.last_decision == ESCALATION.value
    _assert_escalation(workspace_root, cooled.rebase, b'{"phase": "integrating", "target": "main"}')
    reloaded = checkpoint_module.load(workspace_root / "checkpoint.json")
    assert reloaded is not None
    assert reloaded.rebase.last_decision == ESCALATION.value


# --------------------------------------------------------------------------
# Backend read failure driven through the terminal entry point seam.
# --------------------------------------------------------------------------


def test_terminal_entry_point_escalates_to_the_ladder_on_backend_read_failure(
    workspace_root: Path,
) -> None:
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(b'{"phase": "integrating", "target": "main"}')
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration

    recovered = recover_incomplete_integration(
        WorkspaceScope(workspace_root),
        backend=_FailingReadBackend(record_file),
    )
    assert recovered is not None
    assert recovered.last_outcome.value == ESCALATION_OUTCOME
    assert recovered.last_decision == ESCALATION.value
    _assert_escalation(
        workspace_root, recovered, b'{"phase": "integrating", "target": "main"}', phase="complete"
    )
    terminal_state = PipelineState(phase="complete", rebase=recovered)
    assert is_terminal_success_eligible(terminal_state, None, recovered) is False


# --------------------------------------------------------------------------
# Operator-visible label: intervention, not diagnosis/conflict prose.
# --------------------------------------------------------------------------


def test_operator_label_names_the_next_autonomous_action(workspace_root: Path) -> None:
    """The visible label names the ladder escalation, never an intervention.

    The unreadable record escalates autonomously, so the operator-facing
    label must NOT be the retired terminal intervention state.
    """

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), UnifiedConfig()
        )
        assert recovered is not None
        label = format_integration_label(recovered)
        assert label == "evidence diagnosis"
        assert label not in FORBIDDEN_LABELS
        # The persisted checkpoint's label matches the live projection.
        _assert_escalation(workspace_root, recovered, payload)


# --------------------------------------------------------------------------
# Restart / no-repeat: crash + restart keeps the projection, no re-diagnosis.
# --------------------------------------------------------------------------


def test_restart_does_not_repeat_diagnosis_and_keeps_projection(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        # First visit through the real startup seam, then one blocked inner-loop
        # turn that checkpoints the intervention projection. O1 retry-progress
        # rule (D3): the loop returns from ``_continue_after_startup_integration``
        # via the early-return branch without sleeping.
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), UnifiedConfig()
        )
        assert recovered is not None
        state = PipelineState(phase="development", rebase=recovered)
        latest_state: list[PipelineState] = []
        ctx = _loop_context(workspace_root, latest_state, MagicMock())
        first, _previous, _exit = run_loop._run_inner_loop(state, ctx, "development")
        assert first.rebase.last_decision == ESCALATION.value
        checkpoint_path = workspace_root / "checkpoint.json"
        reloaded = checkpoint_module.load(checkpoint_path)
        assert reloaded is not None
        assert reloaded.rebase.last_decision == ESCALATION.value

        # Simulated crash + restart: a FRESH loop context (no in-memory state)
        # reads the same durable evidence through the same real seams. The
        # diagnosis agent stays silent (autouse fixture), the typed decision is
        # identical, the persisted projection survives, and the record bytes are
        # exactly as the crash left them.
        fresh_latest: list[PipelineState] = []
        fresh_ctx = _loop_context(workspace_root, fresh_latest, MagicMock())
        recovered_restart = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), UnifiedConfig()
        )
        assert recovered_restart is not None
        restarted, _previous, _exit = run_loop._run_inner_loop(
            PipelineState(phase="development", rebase=recovered_restart),
            fresh_ctx,
            "development",
        )
        assert restarted.rebase.last_decision == ESCALATION.value
        assert format_integration_label(restarted.rebase) not in FORBIDDEN_LABELS
        outcome_restart = read_record_outcome(workspace_root)
        assert outcome_restart.disposition is RecordReadOutcomeDisposition.UNREADABLE
        assert outcome_restart.unreadable_reason == cause
        assert _record_file(workspace_root).read_bytes() == payload
        reloaded_restart = checkpoint_module.load(checkpoint_path)
        assert reloaded_restart is not None
        assert reloaded_restart.rebase.last_decision == ESCALATION.value
        assert reloaded_restart.rebase.last_outcome.value == ESCALATION_OUTCOME


# --------------------------------------------------------------------------
# ABSENT control: with no record file, the pipeline proceeds.
# --------------------------------------------------------------------------


def test_absent_record_is_the_proceeding_control(workspace_root: Path) -> None:
    assert not _record_file(workspace_root).exists()
    outcome = read_record_outcome(workspace_root)
    assert outcome.disposition is RecordReadOutcomeDisposition.ABSENT
    assert outcome.record is None
    assert outcome.unreadable_reason is None
    recovered = run_loop._run_auto_integrate_recovery_preamble(
        WorkspaceScope(workspace_root), UnifiedConfig()
    )
    # No record: the startup seam has nothing to recover.
    assert recovered is None or not recovered.is_intervention_required
    decision = _authority_decision(recovered if recovered is not None else RebaseState())
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert not _record_file(workspace_root).exists()
