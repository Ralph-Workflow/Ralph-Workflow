from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _interrupted(root: Path, operation: str) -> tuple[str, str, str]:
    assert _git(root, "config", "--replace-all", "core.logAllRefUpdates", "true").returncode == 0
    target = _git(root, "branch", "--show-current").stdout.strip()
    _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", target).returncode == 0
    main = _commit(root, "shared.txt", "main\n")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, operation, target).returncode != 0
    write_record(root, IntegrationRecord(
        phase="integrating", target=target, pre_feature_sha=feature, pre_target_sha=main,
        resolving_rebase=operation == "rebase", resolving_merge=operation == "merge",
    ))
    return target, feature, main


@pytest.mark.parametrize("operation", ["merge", "rebase"])
@pytest.mark.parametrize("seam", ["planning", "after_commit", "boundary"])
def test_retained_resolution_hands_off_and_lands_at_public_integration_seam(
    tmp_git_repo: Path, operation: str, seam: str,
) -> None:
    root = tmp_git_repo
    target, _, _ = _interrupted(root, operation)
    calls: list[str] = []

    def merge_resolver(repo: Path, branch: str) -> bool:
        calls.append(branch)
        (repo / "shared.txt").write_text("resolved feature and main\n", encoding="utf-8")
        return True

    def rebase_resolver(repo: Path, branch: str, _stop: RebaseStop) -> bool:
        return merge_resolver(repo, branch)

    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_target": target, "auto_integrate_remote_enabled": False,
    }})
    if seam == "planning":
        outcome = integrate_before_planning(
            config, WorkspaceScope(root), RebaseState(), conflict_resolver=merge_resolver,
            rebase_stop_resolver=rebase_resolver,
        )
    elif seam == "boundary":
        outcome = auto_integrate_on_phase_transition(
            config, WorkspaceScope(root), RebaseState(), conflict_resolver=merge_resolver,
            rebase_stop_resolver=rebase_resolver,
        )
    else:
        outcome = auto_integrate_after_commit(
            config, WorkspaceScope(root), RebaseState(), conflict_resolver=merge_resolver,
            rebase_stop_resolver=rebase_resolver,
        )
    assert calls == [target]
    assert outcome is not None and outcome.fast_forwarded
    assert _git(root, "rev-parse", target).stdout == _git(root, "rev-parse", "HEAD").stdout
    assert _git(root, "show", f"{target}:shared.txt").stdout == "resolved feature and main\n"
    assert read_record(root) is None


@pytest.mark.parametrize("completion", ["legacy", "prepared", "empty", "unrelated"])
def test_completed_retained_rebase_requires_exact_completion_receipt(tmp_git_repo: Path, completion: str) -> None:
    from ralph.pipeline._pending_rebase_continue import prepare_pending_rebase

    root = tmp_git_repo
    target, _, _ = _interrupted(root, "rebase")
    content = "main\n" if completion == "empty" else "resolved feature and main\n"
    (root / "shared.txt").write_text(content, encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    if completion == "prepared":
        prepare_pending_rebase(root)
    assert _git(root, "-c", "core.editor=true", "rebase", "--continue").returncode == 0
    if completion == "unrelated":
        _commit(root, "unrelated.txt", "later commit\n")
    outcome = integrate_before_planning(
        UnifiedConfig.model_validate({"general": {
            "auto_integrate_target": target, "auto_integrate_remote_enabled": False,
        }}), WorkspaceScope(root), RebaseState(),
    )
    assert outcome is not None
    if completion == "unrelated":
        assert outcome.recovery_record_retained and not outcome.fast_forwarded
        return
    assert outcome.fast_forwarded
    assert _git(root, "rev-parse", target).stdout == _git(root, "rev-parse", "HEAD").stdout
    assert read_record(root) is None


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_aborted_operation_reset_to_target_cannot_be_misreported_as_completed(tmp_git_repo: Path, operation: str) -> None:
    root = tmp_git_repo
    target, feature, main = _interrupted(root, operation)
    saved = read_record(root)
    assert saved is not None
    assert _git(root, operation, "--abort").returncode == 0
    if operation == "merge":
        from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration

        aborted = recover_incomplete_integration(WorkspaceScope(root))
        assert aborted is not None and not aborted.recovery_record_retained
        assert read_record(root) is None
        write_record(root, saved)
    assert _git(root, "reset", "--hard", main).returncode == 0
    outcome = integrate_before_planning(
        UnifiedConfig.model_validate({"general": {
            "auto_integrate_target": target, "auto_integrate_remote_enabled": False,
        }}), WorkspaceScope(root), RebaseState(),
    )
    assert outcome is not None and not outcome.fast_forwarded
    assert outcome.recovery_record_retained
    assert _git(root, "rev-parse", target).stdout.strip() == main
    assert _git(root, "cat-file", "-e", feature).returncode == 0


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_marker_free_interrupted_resolution_rechecks_and_stages_completed_scope(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool, operation: str,
) -> None:
    from ralph.pipeline._pending_merge_commit import mark_pending_merge

    root = tmp_git_repo
    target, _, _ = _interrupted(root, operation)
    if not legacy and operation == "merge":
        mark_pending_merge(root, target, resolving=True)
    (root / "shared.txt").write_text("staged partial\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    (root / "shared.txt").write_text("valuable unstaged completion\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("unfinished user work\n", encoding="utf-8")
    from rich.console import Console

    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import AgentConfig
    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_agent import (
        build_agent_conflict_resolver,
        build_agent_rebase_stop_resolver,
    )
    from ralph.pipeline.conflict_resolution.session import resolution_chain_agents
    from ralph.pipeline.events import PipelineEvent
    from ralph.policy.loader import load_policy
    from tests._pipeline_deps_factory import make_test_pipeline_deps

    calls: list[str] = []

    def execute(*_args: object, **_kwargs: object) -> PipelineEvent:
        calls.append("agent")
        assert (root / "shared.txt").read_text() == "valuable unstaged completion\n"
        prompt = root / ".agent" / "tmp" / "rebase_conflict_resolution_prompt.md"
        assert "shared.txt" in prompt.read_text()
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr("ralph.pipeline.effect_executor.execute_agent_effect", execute)
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_target": target, "auto_integrate_remote_enabled": False,
    }})
    policy = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    registry = AgentRegistry()
    registry.agents.update({name: AgentConfig(cmd="fake") for name in resolution_chain_agents(policy)})
    context = make_display_context(console=Console(file=StringIO()))
    deps = make_test_pipeline_deps(context)
    scope = WorkspaceScope(root)
    display = ParallelDisplay(context)
    merge_resolver = build_agent_conflict_resolver(
        config=config, pipeline_deps=deps, workspace_scope=scope, policy_bundle=policy,
        registry=registry, display=display, display_context=context,
    )
    rebase_resolver = build_agent_rebase_stop_resolver(
        config=config, pipeline_deps=deps, workspace_scope=scope, policy_bundle=policy,
        registry=registry, display=display, display_context=context,
    )
    if legacy:
        from ralph.pipeline.parallel.worker_runtime import run_worker_auto_integration

        outcome = run_worker_auto_integration(
            config=config, workspace_scope=scope, policy_bundle=policy, registry=registry,
            pipeline_deps=deps, display_context=context, recover_first=True,
            state=RebaseState(last_action="conflict", recovery_record_retained=True),
        )
    else:
        outcome = integrate_before_planning(
            config, scope, RebaseState(), conflict_resolver=merge_resolver,
            rebase_stop_resolver=rebase_resolver,
        )
    assert calls == ["agent"]
    assert outcome is not None and outcome.fast_forwarded
    assert _git(root, "show", f"{target}:shared.txt").stdout == "valuable unstaged completion\n"
    assert _git(root, "cat-file", "-e", f"{target}:unrelated.txt").returncode != 0
    assert (root / "unrelated.txt").read_text() == "unfinished user work\n"
    assert read_record(root) is None


def test_foreign_target_commit_repair_uses_git_owner_and_keeps_record_owner(
    tmp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rich.console import Console

    from ralph.display.context import make_display_context
    from ralph.pipeline._pending_merge_repair import repair_pending_merge
    from ralph.pipeline._pending_rebase_continue import prepare_pending_rebase
    from ralph.pipeline.auto_integrate_record import clear_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.events import PipelineEvent
    from ralph.policy.models import PolicyBundle
    from tests._pipeline_deps_factory import make_test_pipeline_deps

    owner = tmp_git_repo
    target, _, _ = _interrupted(owner, "rebase")
    feature = tmp_path / "record-owner"
    assert _git(owner, "worktree", "add", "-b", "record-owner", str(feature), target).returncode == 0
    original_feature = _git(feature, "rev-parse", "HEAD").stdout
    (owner / "shared.txt").write_text("resolved but invalid\n", encoding="utf-8")
    assert _git(owner, "add", "shared.txt").returncode == 0
    prepare_pending_rebase(owner)
    record = read_record(owner)
    assert record is not None and record.rebase_continue_pending
    write_record(feature, record.model_copy(update={
        "target": "feature", "operation_kind": "target_reconcile", "owning_worktree": str(owner),
    }))
    clear_record(owner)
    seen: list[Path] = []

    def execute(
        _effect: object, _config: object, _deps: object, scope: WorkspaceScope, **_kwargs: object,
    ) -> PipelineEvent:
        seen.append(scope.root)
        assert scope.root == owner
        (owner / "shared.txt").write_text("fixed\n", encoding="utf-8")
        (owner / ".agent" / "tmp" / "pending_commit_repair_paths.json").write_text('["shared.txt"]')
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr("ralph.pipeline.effect_executor.execute_agent_effect", execute)
    assert repair_pending_merge(
        workspace_scope=WorkspaceScope(feature), config=UnifiedConfig(),
        pipeline_deps=make_test_pipeline_deps(make_display_context(console=Console(file=StringIO()))),
        policy_bundle=PolicyBundle.model_construct(), display=None, display_context=None,
        agents=("repair",), failure="source validation failed",
    )
    assert seen == [owner]
    assert read_record(owner) is None
    assert read_record(feature) is not None
    result = recover_incomplete_integration(WorkspaceScope(feature))
    assert result is not None and not result.recovery_record_retained
    assert _git(owner, "show", "feature:shared.txt").stdout == "fixed\n"
    assert _git(feature, "rev-parse", "HEAD").stdout == original_feature
    assert read_record(feature) is None


@pytest.mark.parametrize("empty", [False, True])
def test_completed_commit_is_not_replayed_while_clean_landing_receipt_waits(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch, empty: bool,
) -> None:
    from rich.console import Console

    from ralph.config.enums import Verbosity
    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline import run_loop, runner
    from ralph.pipeline.commit_state import CommitState
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy
    from ralph.recovery.controller import RecoveryController
    from ralph.recovery.testing import FakeConnectivityMonitor
    from tests._pipeline_deps_factory import make_test_pipeline_deps

    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "completed feature\n")
    if not empty:
        (root / "shared.txt").write_text("new committed work\n", encoding="utf-8")
    context = make_display_context(console=Console(file=StringIO(), width=120))
    display = ParallelDisplay(context)
    policy = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_target": target, "auto_integrate_remote_enabled": False,
    }})
    scope = WorkspaceScope(root)
    commit_calls: list[str] = []

    def commit(_effect: object, _root: Path) -> PipelineEvent:
        nonlocal feature
        commit_calls.append("commit")
        feature = _commit(root, "shared.txt", "new committed work\n")
        return PipelineEvent.COMMIT_SUCCESS

    target_lock = root / ".git" / "refs" / "heads" / f"{target}.lock"
    waits: list[float] = []

    def sleep(delay: float) -> None:
        waits.append(delay)
        assert read_record(root) is not None
        assert target_lock.exists()
        target_lock.unlink()

    def held(_config: UnifiedConfig, _scope: WorkspaceScope, _state: RebaseState) -> RebaseState:
        target_lock.write_text("another process owns target lock", encoding="utf-8")
        write_record(root, IntegrationRecord(
            phase="integrated", target=target, pre_feature_sha=feature, pre_target_sha=base,
            integrated_feature_sha=feature,
        ))
        return RebaseState(last_action="skipped", last_reason="target lock busy", recovery_record_retained=True)

    deps = replace(
        make_test_pipeline_deps(context), has_uncommitted_changes=lambda _root: not empty,
        commit_effect_executor=commit, auto_integrate_resolver=held,
    )
    initial = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=True))
    controller = RecoveryController()
    completed = runner.run_pipeline_step(
        state=initial, config=config, workspace_scope=scope, policy_bundle=policy,
        registry={}, display=display, display_context=context, verbosity=Verbosity.QUIET,
        pipeline_deps=deps, recovery_controller=controller, pipeline_subscriber=None,
    )
    assert isinstance(completed, PipelineState)
    assert completed.phase != initial.phase
    assert completed.rebase.recovery_record_retained
    assert read_record(root) is not None
    assert commit_calls == ([] if empty else ["commit"])
    phases: list[str] = []

    def next_step(*, state: PipelineState, **_kwargs: object) -> int:
        phases.append(str(state.phase))
        assert state.phase != initial.phase
        assert read_record(root) is None
        assert _git(root, "rev-parse", target).stdout.strip() == feature
        return 0

    monkeypatch.setattr(runner, "run_pipeline_step", next_step)
    ctx = run_loop._LoopContext(
        policy_bundle=policy, workspace_scope=scope, config=config, active_display=display,
        display_context=context, effective_verbosity=Verbosity.QUIET, registry={},
        effective_pipeline_subscriber=None, controller=controller, config_path=None,
        cli_overrides={}, monitor_stop=None, connectivity_monitor=FakeConnectivityMonitor(),
        sleep=sleep, is_quiet=True, pipeline_deps=replace(deps, auto_integrate_resolver=None),
    )
    _, _, code = run_loop._run_inner_loop_after_startup(completed, ctx, str(initial.phase))
    assert code == 0
    assert phases == [completed.phase]
    assert waits == [5.0]


@pytest.mark.parametrize("receipt", ["integrating", "integrated", "malformed", "missing_sha"])
def test_durable_ownership_blocks_clean_dispatch_and_survives_disable(
    tmp_git_repo: Path, receipt: str,
) -> None:
    from ralph.pipeline.auto_integrate_record import clear_record, record_path
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.integration_resolution import inspect_integration_resolution

    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    write_record(root, IntegrationRecord(
        phase="integrating" if receipt == "integrating" else "integrated", target=target,
        pre_feature_sha=feature, pre_target_sha=base,
        integrated_feature_sha=feature if receipt == "integrated" else None,
    ))
    if receipt == "malformed":
        record_path(root).write_text('{"phase":', encoding="utf-8")
    original = record_path(root).read_bytes()
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_enabled": False, "auto_integrate_target": target,
        "auto_integrate_remote_enabled": False,
    }})
    recovered = integrate_before_planning(config, WorkspaceScope(root), RebaseState())
    assert recovered is not None
    if receipt in {"malformed", "missing_sha"}:
        assert recovered.recovery_record_retained
        assert record_path(root).read_bytes() == original
        direct = recover_incomplete_integration(WorkspaceScope(root), config=config)
        assert direct is not None and direct.recovery_record_retained
        assert _git(root, "rev-parse", target).stdout.strip() == base
        assert not inspect_integration_resolution(root, recovered).dispatch_allowed
    else:
        assert recovered.fast_forwarded
        assert read_record(root) is None
        assert _git(root, "rev-parse", target).stdout.strip() == feature
        assert inspect_integration_resolution(root, recovered).dispatch_allowed
    clear_record(root)
    assert inspect_integration_resolution(root, RebaseState(recovery_record_retained=True)).dispatch_allowed
