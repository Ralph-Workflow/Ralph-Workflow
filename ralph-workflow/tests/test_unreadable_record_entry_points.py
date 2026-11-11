from __future__ import annotations

import contextlib
import shutil
from collections.abc import Callable, Generator
from functools import cache
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
ESCALATION = PhaseDecision.BLOCK_FOR_RECOVERY
ESCALATION_OUTCOME = "ladder_escalation"
FORBIDDEN_LABELS = ("intervention",)


class _LoopBudgetExhausted(BaseException):
    pass


def _bounded_clock() -> MagicMock:
    return MagicMock(side_effect=_LoopBudgetExhausted)


class _FailingReadBackend(PathFileBackend):
    def __init__(self, record_file: Path) -> None:
        self._record_file = record_file

    def exists(self, path: Path) -> bool:
        return path == self._record_file

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        raise PermissionError(13, "Permission denied", str(path))


def _record_file(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME


def _seed_unreadable_record(workspace_root: Path, cause: str) -> bytes:
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


def _ignore_inert_git_samples(directory: str, names: list[str]) -> set[str]:
    del names
    return {"hooks"} if Path(directory).name == ".git" else set()


@pytest.fixture(scope="session")
def _git_worktree_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from git import Repo

    root = tmp_path_factory.mktemp("unreadable-record-template")
    repo = Repo.init(root, initial_branch="main")
    try:
        writer = repo.config_writer()
        try:
            writer.set_value("user", "name", "test")
            writer.set_value("user", "email", "test@example.com")
        finally:
            writer.release()
    finally:
        repo.close()
    return root


@pytest.fixture
def workspace_root(
    tmp_path: Path,
    _git_worktree_template: Path,
) -> Generator[Path, None, None]:
    shutil.copytree(
        _git_worktree_template,
        tmp_path,
        dirs_exist_ok=True,
        ignore=_ignore_inert_git_samples,
    )
    yield tmp_path


@pytest.fixture(autouse=True)
def no_diagnosis_agent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
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
    assert isinstance(value, MagicMock), f"expected MagicMock, got {type(value).__name__}"
    return value


@cache
def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_remote_enabled": False}})


@cache
def _default_policy_bundle() -> PolicyBundle:
    repo_defaults = Path(__file__).parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(repo_defaults)


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
    return _default_policy_bundle().model_copy(update={"pipeline": policy})


def _loop_context(
    workspace_root: Path,
    latest_state: list[PipelineState],
    sleep: Callable[[float], None],
) -> run_loop._LoopContext:
    return run_loop._LoopContext(
        policy_bundle=_policy_bundle(),
        workspace_scope=WorkspaceScope(workspace_root),
        config=_config(),
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
    decision = _authority_decision(rebase, phase=phase)
    assert decision is ESCALATION
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert rebase.is_intervention_required is True
    assert rebase.last_outcome is not None
    assert rebase.last_outcome.value == ESCALATION_OUTCOME
    label = format_integration_label(rebase)
    assert label not in FORBIDDEN_LABELS
    assert _record_file(workspace_root).read_bytes() == payload
    assert not inspect_integration_resolution(workspace_root, rebase).dispatch_allowed
    assert retained_integration_reason(workspace_root) is not None
    return decision


def test_unreadable_outcome_is_typed_with_reason(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        outcome = read_record_outcome(workspace_root)
        assert outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE
        assert outcome.unreadable_reason == cause
        assert outcome.record is None
        assert _record_file(workspace_root).read_bytes() == payload


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


def test_unreadable_evidence_is_preserved_not_cleared(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        store = IntegrationRecordStore(workspace_root)
        with pytest.raises(RecordUnreadableError):
            store.transition(START_INTEGRATING)
        with pytest.raises(RecordUnreadableError):
            store.transition(CLEAR)
        assert _record_file(workspace_root).read_bytes() == payload


def test_startup_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), _config()
        )
        assert recovered is not None
        assert recovered.last_outcome.value == ESCALATION_OUTCOME
        assert recovered.last_decision == ESCALATION.value
        _assert_escalation(workspace_root, recovered, payload)


def test_phase_boundary_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), _config()
        )
        assert recovered is not None
        state = PipelineState(phase="development", rebase=recovered)
        latest_state: list[PipelineState] = []
        sleep = _bounded_clock()
        ctx = _loop_context(workspace_root, latest_state, sleep)
        with contextlib.suppress(_LoopBudgetExhausted):
            run_loop._run_inner_loop(state, ctx, "development")
        assert sleep.called, (
            "repeat escalations must pace the loop with the bounded retry "
            "instead of returning early on a person"
        )
        assert all(call.args[0] > 0 for call in sleep.call_args_list)
        result_state = latest_state[0]
        assert result_state.rebase.last_decision == ESCALATION.value
        _assert_escalation(workspace_root, result_state.rebase, payload)
        reloaded = checkpoint_module.load(workspace_root / "checkpoint.json")
        assert reloaded is not None
        assert reloaded.rebase.last_decision == ESCALATION.value


def test_commit_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        deps = MagicMock()
        deps.policy_bundle = _policy_bundle()
        deps.policy_bundle_factory = None
        verdict = prepare_commit_integration(
            workspace_root,
            _config(),
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
            config=_config(),
            workspace_scope=WorkspaceScope(workspace_root),
            state=PipelineState(phase="development", rebase=RebaseState()),
            display=MagicMock(),
            policy_bundle=bundle,
            registry=MagicMock(),
        )
        assert outcome is not None
        assert outcome.last_outcome.value == ESCALATION_OUTCOME
        _assert_escalation(workspace_root, outcome, payload)


def test_cooldown_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), _config()
        )
        assert recovered is not None
        state = PipelineState(phase="development", rebase=recovered)
        latest_state: list[PipelineState] = [state]
        sleep = MagicMock()
        ctx = _loop_context(workspace_root, latest_state, sleep)
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


def test_parallel_join_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        joined = run_worker_auto_integration(
            config=_config(),
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


def test_terminal_entry_point_escalates_to_the_ladder(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), _config()
        )
        assert recovered is not None
        terminal_state = PipelineState(phase="complete", rebase=recovered)
        assert is_terminal_success_eligible(terminal_state, None, recovered) is False
        decision = _authority_decision(recovered, phase="complete")
        assert decision is ESCALATION
        _assert_escalation(workspace_root, recovered, payload, phase="complete")


def test_startup_entry_point_escalates_to_the_ladder_on_backend_read_failure(
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
    _assert_escalation(workspace_root, recovered, b'{"phase": "integrating", "target": "main"}')


def test_phase_boundary_entry_point_escalates_to_the_ladder_on_backend_read_failure(
    workspace_root: Path,
) -> None:
    record_file = _record_file(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(b'{"phase": "integrating", "target": "main"}')
    state = PipelineState(phase="development", rebase=RebaseState())
    latest_state: list[PipelineState] = []
    sleep = _bounded_clock()
    ctx = _loop_context(workspace_root, latest_state, sleep)
    with contextlib.suppress(_LoopBudgetExhausted):
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


def test_cooldown_entry_point_escalates_to_the_ladder_on_backend_read_failure(
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
    state = PipelineState(phase="development", rebase=recovered)
    latest_state: list[PipelineState] = [state]
    sleep = MagicMock()
    ctx = _loop_context(workspace_root, latest_state, sleep)
    cooled = run_loop._wait_for_integration_retry(
        state,
        ctx,
        backend=_FailingReadBackend(record_file),
    )
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


def test_operator_label_names_the_next_autonomous_action(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), _config()
        )
        assert recovered is not None
        label = format_integration_label(recovered)
        assert label == "evidence diagnosis"
        assert label not in FORBIDDEN_LABELS
        _assert_escalation(workspace_root, recovered, payload)


def test_restart_does_not_repeat_diagnosis_and_keeps_projection(workspace_root: Path) -> None:

    for cause in UNREADABLE_CAUSES:
        payload = _seed_unreadable_record(workspace_root, cause)
        recovered = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), _config()
        )
        assert recovered is not None
        state = PipelineState(phase="development", rebase=recovered)
        latest_state: list[PipelineState] = []
        sleep = _bounded_clock()
        ctx = _loop_context(workspace_root, latest_state, sleep)
        with contextlib.suppress(_LoopBudgetExhausted):
            run_loop._run_inner_loop(state, ctx, "development")
        assert sleep.call_count == 1, (
            "the blocked loop must pace its repeat escalation with the "
            "bounded retry (the fake clock ends it at the first paced sleep)"
        )
        assert all(call.args[0] > 0 for call in sleep.call_args_list)
        first = latest_state[0]
        assert first.rebase.last_decision == ESCALATION.value
        checkpoint_path = workspace_root / "checkpoint.json"
        reloaded = checkpoint_module.load(checkpoint_path)
        assert reloaded is not None
        assert reloaded.rebase.last_decision == ESCALATION.value

        fresh_latest: list[PipelineState] = []
        fresh_sleep = _bounded_clock()
        fresh_ctx = _loop_context(workspace_root, fresh_latest, fresh_sleep)
        recovered_restart = run_loop._run_auto_integrate_recovery_preamble(
            WorkspaceScope(workspace_root), _config()
        )
        assert recovered_restart is not None
        with contextlib.suppress(_LoopBudgetExhausted):
            run_loop._run_inner_loop(
                PipelineState(phase="development", rebase=recovered_restart),
                fresh_ctx,
                "development",
            )
        assert fresh_sleep.call_count == 1, (
            "the restarted loop must pace its repeat escalation with the "
            "bounded retry (the fake clock ends it at the first paced sleep)"
        )
        restarted = fresh_latest[0]
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


def test_absent_record_is_the_proceeding_control(workspace_root: Path) -> None:
    assert not _record_file(workspace_root).exists()
    outcome = read_record_outcome(workspace_root)
    assert outcome.disposition is RecordReadOutcomeDisposition.ABSENT
    assert outcome.record is None
    assert outcome.unreadable_reason is None
    recovered = run_loop._run_auto_integrate_recovery_preamble(
        WorkspaceScope(workspace_root), _config()
    )
    assert recovered is None or not recovered.is_intervention_required
    decision = _authority_decision(recovered if recovered is not None else RebaseState())
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert not _record_file(workspace_root).exists()
