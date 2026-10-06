"""Pending commit repair hands off Git's failure without restarting resolution."""

from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate_record import IntegrationRecord, record_path
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_auto_integrate_record_idempotent import _RecordBackend

if TYPE_CHECKING:
    from ralph.pipeline import run_loop


@pytest.mark.parametrize("agent_fault", [False, True])
@pytest.mark.parametrize("rebase", [False, True])
def test_repair_handoff_preserves_snapshot_and_rotates_after_failed_agent(
    agent_fault: bool,
    rebase: bool,
) -> None:
    from ralph.pipeline._pending_merge_repair import handoff_pending_merge_repair

    root = Path("/workspace")
    backend = _RecordBackend()
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="original",
        pre_target_sha="target",
        merge_commit_pending=True,
        merge_commit_head="head",
        merge_commit_parent="parent",
        merge_commit_tree="tree",
        rebase_continue_pending=rebase,
        rebase_continue_head="rebase-head",
        rebase_continue_stop="stop",
        rebase_continue_tree="rebase-tree",
    )
    received: list[tuple[str, str]] = []

    def invoke(agent: str, prompt: Path) -> bool:
        received.append((agent, backend.read_text(prompt)))
        if agent_fault:
            raise RuntimeError("agent transport unavailable")
        return False

    for _ in range(2):
        handoff_pending_merge_repair(
            root=root,
            record=record,
            failure="pre-commit: formatter executable missing",
            agents=("first", "second"),
            invoke=invoke,
            backend=backend,
        )
        record = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))

    assert [agent for agent, _ in received] == ["first", "second"]
    assert record.merge_commit_tree == "tree"
    assert record.merge_commit_pending
    for _, prompt in received:
        assert ("rebase continuation" if rebase else "merge commit") in prompt
        assert "pre-commit: formatter executable missing" in prompt
        assert "Do not abort" in prompt
        assert "Do not bypass" in prompt
        assert "Ralph will retry" in prompt


def test_missing_repair_agent_keeps_pending_record_without_claiming_completion() -> None:
    from ralph.pipeline._pending_merge_repair import handoff_pending_merge_repair

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="original",
        pre_target_sha="target",
        merge_commit_pending=True,
    )

    def invoke(agent: str, prompt: Path) -> bool:
        raise AssertionError(f"unexpected invocation: {agent} {prompt}")

    assert not handoff_pending_merge_repair(
        root=Path("/workspace"),
        record=record,
        failure="hook rejected commit",
        agents=(),
        invoke=invoke,
        backend=_RecordBackend(),
    )


def test_runtime_recovery_waits_when_another_integration_owner_holds_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.config.verbosity import Verbosity
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline import run_loop
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
    from ralph.recovery.testing import FakeConnectivityMonitor
    from ralph.workspace.scope import WorkspaceScope

    @contextmanager
    def contended(_root: Path) -> Iterator[bool]:
        yield False

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_transaction.integration_transaction", contended
    )
    output = StringIO()
    display_context = make_display_context(console=Console(file=output, width=180))
    display = ParallelDisplay(display_context)
    registry: dict[str, AgentConfig] = {}
    context = run_loop._LoopContext(
        policy_bundle=PolicyBundle.model_construct(),
        workspace_scope=WorkspaceScope(Path("/workspace")),
        config=UnifiedConfig(),
        active_display=display,
        display_context=display_context,
        effective_verbosity=Verbosity.QUIET,
        registry=registry,
        effective_pipeline_subscriber=None,
        controller=RecoveryController(options=RecoveryControllerOptions(cycle_cap=1)),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=FakeConnectivityMonitor(),
        sleep=lambda _seconds: None,
        is_quiet=True,
    )
    assert not run_loop._run_integration_conflict_resolution(context)
    assert "integration recovery retained; another owner is active" in output.getvalue()


def test_commit_repair_uses_supervised_resolution_session_and_retains_pending_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.pipeline._pending_repair_edits as repair_edits
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline._pending_merge_repair import repair_pending_merge
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.events import PipelineEvent
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

    backend = _RecordBackend()
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="original",
        pre_target_sha="target",
        merge_commit_pending=True,
        merge_commit_tree="tree",
    )
    received: list[InvokeAgentEffect] = []

    def execute(effect: InvokeAgentEffect, *_args: object, **_kwargs: object) -> PipelineEvent:
        received.append(effect)
        assert "hook: formatter missing" in backend.read_text(Path(effect.prompt_file))
        return PipelineEvent.AGENT_SUCCESS

    @contextmanager
    def lease(_root: Path) -> Iterator[bool]:
        yield True

    def read_owned_record(_root: Path) -> IntegrationRecord:
        return record

    def matches(_root: Path, _record: IntegrationRecord) -> bool:
        return True

    def capture_work(_root: Path) -> dict[str, str]:
        return {}

    def capture_controls(_root: Path, _backend: object = None) -> str:
        return "hooks"

    monkeypatch.setattr("ralph.pipeline.auto_integrate_transaction.integration_transaction", lease)
    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", read_owned_record)
    monkeypatch.setattr(
        "ralph.pipeline._pending_merge_commit.pending_merge_identity_matches",
        matches,
    )
    monkeypatch.setattr("ralph.pipeline.effect_executor.execute_agent_effect", execute)
    monkeypatch.setattr(repair_edits, "capture_unstaged_work", capture_work)
    monkeypatch.setattr(repair_edits, "capture_commit_controls", capture_controls)
    monkeypatch.setattr(repair_edits, "pending_merge_identity_matches", matches)
    root = Path("/workspace")
    assert repair_pending_merge(
        workspace_scope=WorkspaceScope(root),
        config=UnifiedConfig(),
        pipeline_deps=make_test_pipeline_deps(
            make_display_context(console=Console(file=StringIO()))
        ),
        policy_bundle=PolicyBundle.model_construct(),
        display=None,
        display_context=None,
        agents=("repair-agent",),
        failure="hook: formatter missing",
        backend=backend,
    )
    assert len(received) == 1
    assert received[0].agent_name == "repair-agent"
    assert received[0].phase == "rebase_conflict_resolution"
    assert received[0].requires_completion_evidence
    assert received[0].activity_only_supervision
    persisted = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert persisted.merge_commit_pending
    assert persisted.merge_commit_tree == "tree"


@pytest.mark.parametrize("operation", ["merge", "rebase"])
@pytest.mark.parametrize("evidence", ["no_live_operation", "staged_live", "unmerged_live"])
def test_runtime_retained_resolution_retries_owner_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    evidence: str,
) -> None:
    """The durable owner reaches continuation even when porcelain and markers are clean."""
    from ralph.git.merge import MERGE_STATE_IN_PROGRESS, MERGE_STATE_NONE
    from ralph.pipeline import run_loop
    from ralph.pipeline.integration_resolution import inspect_integration_resolution
    from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.state import PipelineState

    context = _retained_runtime_context()
    record: IntegrationRecord | None = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="original",
        pre_target_sha="target",
        resolving_merge=operation == "merge",
        resolving_rebase=operation == "rebase",
    )
    retained = RebaseState(
        last_action="skipped",
        last_target="main",
        recovery_record_retained=True,
        last_reason="integration resolution interrupted; retained for agent continuation",
    )
    state = PipelineState(phase="planning", rebase=retained)
    attempts: list[str] = []
    saved: list[PipelineState] = []

    def read(_root: Path) -> IntegrationRecord | None:
        return record

    def inspect(root: Path, rebase: RebaseState) -> IntegrationResolutionVerdict:
        active = record is not None and evidence != "no_live_operation"
        porcelain = "UU shared.txt\n" if active and evidence == "unmerged_live" else ""

        def porcelain_state(_root: Path) -> tuple[bool, str]:
            return True, porcelain

        def rebase_active(_root: Path) -> bool:
            return active and operation == "rebase"

        def merge_state(_root: Path) -> str:
            return MERGE_STATE_IN_PROGRESS if active and operation == "merge" else MERGE_STATE_NONE

        return inspect_integration_resolution(
            root,
            rebase,
            porcelain=porcelain_state,
            rebase_active=rebase_active,
            merge_status=merge_state,
        )

    def continue_owned(_context: run_loop._LoopContext, _previous: RebaseState) -> RebaseState:
        nonlocal record
        assert record is not None, "continuation must run under durable ownership"
        attempts.append(operation)
        if len(attempts) == 1:
            return retained
        record = None
        return RebaseState(last_action="recovered", last_target="main", fast_forwarded=True)

    def bypass(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError(
            "owned resolution must use its continuation seam, not restart a resolver"
        )

    def checkpoint(current: PipelineState, _context: run_loop._LoopContext) -> None:
        saved.append(current)

    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", read)
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", inspect)
    monkeypatch.setattr(run_loop, "_run_startup_integration", continue_owned)
    monkeypatch.setattr(run_loop, "_run_integration_conflict_resolution", bypass)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", checkpoint)
    monkeypatch.setattr(run_loop, "_repair_pending_merge_commit", bypass)

    blocked = run_loop._block_unresolved_integration(state, context, "planning")
    assert blocked is not None and blocked[0].rebase.recovery_record_retained
    assert attempts == [operation]
    assert run_loop._block_unresolved_integration(blocked[0], context, "planning") is None
    assert attempts == [operation, operation]
    assert saved[-1].rebase.fast_forwarded and not saved[-1].rebase.recovery_record_retained
    assert run_loop._block_unresolved_integration(saved[-1], context, "planning") is None
    assert attempts == [operation, operation]


def _retained_runtime_context() -> "run_loop._LoopContext":
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.config.verbosity import Verbosity
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline import run_loop
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
    from ralph.recovery.testing import FakeConnectivityMonitor
    from ralph.workspace.scope import WorkspaceScope

    display_context = make_display_context(console=Console(file=StringIO(), width=180))
    registry: dict[str, AgentConfig] = {}

    def sleep(_seconds: float) -> None:
        return None

    return run_loop._LoopContext(
        policy_bundle=PolicyBundle.model_construct(),
        workspace_scope=WorkspaceScope(Path("/workspace")),
        config=UnifiedConfig.model_validate(
            {"general": {"auto_integrate_enabled": True, "auto_integrate_target": "main"}}
        ),
        active_display=ParallelDisplay(display_context),
        display_context=display_context,
        effective_verbosity=Verbosity.QUIET,
        registry=registry,
        effective_pipeline_subscriber=None,
        controller=RecoveryController(options=RecoveryControllerOptions(cycle_cap=1)),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=FakeConnectivityMonitor(),
        sleep=sleep,
        is_quiet=True,
    )
