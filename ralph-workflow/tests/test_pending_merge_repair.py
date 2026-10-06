"""Pending commit repair hands off Git's failure without restarting resolution."""

from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate_record import IntegrationRecord, record_path
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_auto_integrate_record_idempotent import _RecordBackend


@pytest.mark.parametrize("agent_fault", [False, True])
@pytest.mark.parametrize("rebase", [False, True])
def test_repair_handoff_preserves_snapshot_and_rotates_after_failed_agent(
    agent_fault: bool, rebase: bool,
) -> None:
    from ralph.pipeline._pending_merge_repair import handoff_pending_merge_repair

    root = Path("/workspace")
    backend = _RecordBackend()
    record = IntegrationRecord(
        phase="integrating", target="main", pre_feature_sha="original",
        pre_target_sha="target", merge_commit_pending=True,
        merge_commit_head="head", merge_commit_parent="parent", merge_commit_tree="tree",
        rebase_continue_pending=rebase, rebase_continue_head="rebase-head",
        rebase_continue_stop="stop", rebase_continue_tree="rebase-tree",
    )
    received: list[tuple[str, str]] = []

    def invoke(agent: str, prompt: Path) -> bool:
        received.append((agent, backend.read_text(prompt)))
        if agent_fault:
            raise RuntimeError("agent transport unavailable")
        return False

    for _ in range(2):
        handoff_pending_merge_repair(
            root=root, record=record, failure="pre-commit: formatter executable missing",
            agents=("first", "second"), invoke=invoke, backend=backend,
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
        phase="integrating", target="main", pre_feature_sha="original",
        pre_target_sha="target", merge_commit_pending=True,
    )

    def invoke(agent: str, prompt: Path) -> bool:
        raise AssertionError(f"unexpected invocation: {agent} {prompt}")

    assert not handoff_pending_merge_repair(
        root=Path("/workspace"), record=record, failure="hook rejected commit",
        agents=(), invoke=invoke, backend=_RecordBackend(),
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

    monkeypatch.setattr("ralph.pipeline.auto_integrate_transaction.integration_transaction", contended)
    output = StringIO()
    display_context = make_display_context(console=Console(file=output, width=180))
    display = ParallelDisplay(display_context)
    registry: dict[str, AgentConfig] = {}
    context = run_loop._LoopContext(
        policy_bundle=PolicyBundle.model_construct(), workspace_scope=WorkspaceScope(Path("/workspace")),
        config=UnifiedConfig(), active_display=display, display_context=display_context,
        effective_verbosity=Verbosity.QUIET, registry=registry,
        effective_pipeline_subscriber=None, controller=RecoveryController(options=RecoveryControllerOptions(cycle_cap=1)),
        config_path=None, cli_overrides={}, monitor_stop=None,
        connectivity_monitor=FakeConnectivityMonitor(), sleep=lambda _seconds: None, is_quiet=True,
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
        phase="integrating", target="main", pre_feature_sha="original",
        pre_target_sha="target", merge_commit_pending=True, merge_commit_tree="tree",
    )
    received: list[InvokeAgentEffect] = []

    def execute(effect: InvokeAgentEffect, *_args: object, **_kwargs: object) -> PipelineEvent:
        received.append(effect)
        assert "hook: formatter missing" in backend.read_text(Path(effect.prompt_file))
        return PipelineEvent.AGENT_SUCCESS

    @contextmanager
    def lease(_root: Path) -> Iterator[bool]:
        yield True

    monkeypatch.setattr("ralph.pipeline.auto_integrate_transaction.integration_transaction", lease)
    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", lambda _root: record)
    monkeypatch.setattr(
        "ralph.pipeline._pending_merge_commit.pending_merge_identity_matches",
        lambda _root, _record: True,
    )
    monkeypatch.setattr("ralph.pipeline.effect_executor.execute_agent_effect", execute)
    monkeypatch.setattr(repair_edits, "capture_unstaged_work", lambda _root: {})
    monkeypatch.setattr(repair_edits, "capture_commit_controls", lambda *_args: "hooks")
    monkeypatch.setattr(repair_edits, "pending_merge_identity_matches", lambda _root, _record: True)
    root = Path("/workspace")
    assert repair_pending_merge(
        workspace_scope=WorkspaceScope(root), config=UnifiedConfig(),
        pipeline_deps=make_test_pipeline_deps(make_display_context(console=Console(file=StringIO()))),
        policy_bundle=PolicyBundle.model_construct(),
        display=None, display_context=None, agents=("repair-agent",),
        failure="hook: formatter missing", backend=backend,
    )
    assert len(received) == 1
    assert received[0].agent_name == "repair-agent"
    assert received[0].phase == "rebase_conflict_resolution"
    assert received[0].requires_completion_evidence
    assert received[0].activity_only_supervision
    persisted = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert persisted.merge_commit_pending
    assert persisted.merge_commit_tree == "tree"
