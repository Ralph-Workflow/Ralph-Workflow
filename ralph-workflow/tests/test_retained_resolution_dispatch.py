from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_pending_merge_commit_recovery import _commit, _git

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


@pytest.mark.parametrize("empty", [False, True])
def test_completed_commit_is_not_replayed_while_clean_landing_receipt_waits(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    empty: bool,
) -> None:
    from rich.console import Console

    from ralph.config.enums import Verbosity
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline import run_loop, runner
    from ralph.pipeline.commit_state import CommitState
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy
    from ralph.recovery.controller import RecoveryController
    from ralph.recovery.testing import FakeConnectivityMonitor

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
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
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
        write_record(
            root,
            IntegrationRecord(
                phase="integrated",
                target=target,
                pre_feature_sha=feature,
                pre_target_sha=base,
                integrated_feature_sha=feature,
            ),
        )
        return RebaseState(
            last_action="skipped", last_reason="target lock busy", recovery_record_retained=True
        )

    deps = replace(
        make_test_pipeline_deps(context),
        has_uncommitted_changes=lambda _root: not empty,
        commit_effect_executor=commit,
        auto_integrate_resolver=held,
    )
    initial = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=True))
    controller = RecoveryController()
    completed = runner.run_pipeline_step(
        state=initial,
        config=config,
        workspace_scope=scope,
        policy_bundle=policy,
        registry={},
        display=display,
        display_context=context,
        verbosity=Verbosity.QUIET,
        pipeline_deps=deps,
        recovery_controller=controller,
        pipeline_subscriber=None,
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
        policy_bundle=policy,
        workspace_scope=scope,
        config=config,
        active_display=display,
        display_context=context,
        effective_verbosity=Verbosity.QUIET,
        registry={},
        effective_pipeline_subscriber=None,
        controller=controller,
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=FakeConnectivityMonitor(),
        sleep=sleep,
        is_quiet=True,
        pipeline_deps=replace(deps, auto_integrate_resolver=None),
    )
    _, _, code = run_loop._run_inner_loop_after_startup(completed, ctx, str(initial.phase))
    assert code == 0
    assert phases == [completed.phase]
    assert waits == [5.0]


@pytest.mark.parametrize("receipt", ["integrating", "integrated", "malformed", "missing_sha"])
def test_durable_ownership_blocks_clean_dispatch_and_survives_disable(
    tmp_git_repo: Path,
    receipt: str,
) -> None:
    from ralph.pipeline.auto_integrate_record import clear_record, record_path

    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    write_record(
        root,
        IntegrationRecord(
            phase="integrating" if receipt == "integrating" else "integrated",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=base,
            integrated_feature_sha=feature if receipt == "integrated" else None,
        ),
    )
    if receipt == "malformed":
        record_path(root).write_text('{"phase":', encoding="utf-8")
    original = record_path(root).read_bytes()
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": False,
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
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
    assert inspect_integration_resolution(
        root, RebaseState(recovery_record_retained=True)
    ).dispatch_allowed
