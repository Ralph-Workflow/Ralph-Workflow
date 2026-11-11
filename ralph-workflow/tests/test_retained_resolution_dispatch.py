from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from functools import cache
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline import run_loop, runner
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.commit_state import CommitState
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.recovery.controller import RecoveryController
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_pending_merge_commit_recovery import _git

pytestmark = [pytest.mark.timeout_seconds(10)]


@pytest.fixture(autouse=True)
def _patch_lock_recovery_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.git._lock_recovery._sleep_fn", lambda _s: None)
    monkeypatch.setattr("ralph.git.operations._sleep_fn", lambda _s: None)
    monkeypatch.setattr("ralph.git._lock_recovery._PROBE_TIMEOUT_S", 0.001)
    monkeypatch.setattr("ralph.git._lock_recovery._LIVE_HOLDER_POLL_S", 0.001)
    monkeypatch.setattr("ralph.git._lock_recovery._LIVE_HOLDER_PATIENCE_S", 0.001)
    monkeypatch.setattr("ralph.git._lock_recovery._LOCK_BACKOFF_S", ())
    monkeypatch.setattr("ralph.git._lock_recovery._probe_lsof_fuser", lambda _p, _t: "dead")


def _write_commit_message_when_needed(workspace: FsWorkspace, *, empty: bool) -> None:
    if not empty:
        workspace.write(
            ".agent/artifacts/commit_message.md",
            "---\ntype: commit\nsubject: fix: complete retained resolution\n---\n",
        )


@cache
def _get_default_policy():
    from ralph.policy.loader import load_policy

    return load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")


_SCENARIO_FEATURE_CONTENT: dict[str, str] = {
    "completed_commit": "completed feature\n",
    "durable_ownership": "feature\n",
}


def _current_branch(root: Path) -> str:
    head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    prefix = "ref: refs/heads/"
    assert head.startswith(prefix), f"expected an attached HEAD, got: {head}"
    return head[len(prefix) :]


def _branch_sha(root: Path, branch: str) -> str:
    ref = root / ".git" / "refs" / "heads" / branch
    if not ref.is_file():
        return _git(root, "rev-parse", branch).stdout.strip()
    return ref.read_text(encoding="utf-8").strip()


def _commit(root: Path, path: str, content: str) -> str:
    from git import Repo

    FsWorkspace(root).write(path, content)
    repo = Repo(root)
    try:
        repo.index.add([path])
        commit = repo.index.commit(path)
        assert commit.hexsha
        return commit.hexsha
    finally:
        repo.close()


def _seed_commit(root: Path, path: str, content: str) -> str:
    from git import Repo

    FsWorkspace(root).write(path, content)
    repo = Repo(root)
    try:
        repo.index.add([path])
        return repo.index.commit(path).hexsha
    finally:
        repo.close()


@dataclass(frozen=True)
class _Scenario:
    root: Path
    target: str
    base: str
    feature: str


@pytest.fixture(scope="session")
def _scenario_templates(
    tmp_path_factory: pytest.TempPathFactory,
    _git_repo_template: Path,
) -> dict[str, _Scenario]:
    from git import Repo

    scenarios: dict[str, _Scenario] = {}
    for scenario, feature_content in _SCENARIO_FEATURE_CONTENT.items():
        root = tmp_path_factory.mktemp(f"retained-resolution-{scenario}")
        shutil.copytree(_git_repo_template, root, dirs_exist_ok=True)
        target = _current_branch(root)
        base = _seed_commit(root, "shared.txt", "base\n")
        repo = Repo(root)
        try:
            repo.create_head("feature", base)
            repo.head.set_reference(repo.heads.feature)
        finally:
            repo.close()
        assert _current_branch(root) == "feature"
        assert _branch_sha(root, "feature") == base
        feature = _seed_commit(root, "shared.txt", feature_content)
        scenarios[scenario] = _Scenario(
            root=root,
            target=target,
            base=base,
            feature=feature,
        )
    return scenarios


def _scenario_repo(tmp_path: Path, scenarios: dict[str, _Scenario], scenario: str) -> _Scenario:
    prepared = scenarios[scenario]
    root = tmp_path / "repo"
    shutil.copytree(prepared.root, root)
    return _Scenario(
        root=root,
        target=prepared.target,
        base=prepared.base,
        feature=prepared.feature,
    )


@pytest.fixture
def completed_commit_repo(tmp_path: Path, _scenario_templates: dict[str, _Scenario]) -> _Scenario:
    return _scenario_repo(tmp_path, _scenario_templates, "completed_commit")


@pytest.fixture
def durable_ownership_repo(tmp_path: Path, _scenario_templates: dict[str, _Scenario]) -> _Scenario:
    return _scenario_repo(tmp_path, _scenario_templates, "durable_ownership")


@pytest.mark.parametrize("empty", [False, True])
def test_completed_commit_is_not_replayed_while_clean_landing_receipt_waits(
    completed_commit_repo: _Scenario,
    monkeypatch: pytest.MonkeyPatch,
    empty: bool,
) -> None:
    root = completed_commit_repo.root
    workspace = FsWorkspace(root)
    target = completed_commit_repo.target
    base = completed_commit_repo.base
    feature = completed_commit_repo.feature
    if not empty:
        workspace.write("shared.txt", "new committed work\n")
    context = make_display_context(console=Console(file=StringIO(), width=120))
    display = ParallelDisplay(context)
    policy = _get_default_policy()
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
        workspace.write(f".git/refs/heads/{target}.lock", "another process owns target lock")
        write_record(
            root,
            IntegrationRecord(
                phase="integrated",
                target=target,
                pre_feature_sha=feature,
                pre_target_sha=base,
                integrated_feature_sha=feature,
                owning_worktree=str(root),
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
    _write_commit_message_when_needed(workspace, empty=empty)
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
    durable_ownership_repo: _Scenario,
    receipt: str,
) -> None:
    from ralph.pipeline.auto_integrate_record import (
        RecordUnreadableError,
        clear_record,
        record_path,
    )

    root = durable_ownership_repo.root
    target = durable_ownership_repo.target
    base = durable_ownership_repo.base
    feature = durable_ownership_repo.feature
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
        DEFAULT_FILE_BACKEND.write_text(record_path(root), '{"phase":', encoding="utf-8")
    original = DEFAULT_FILE_BACKEND.read_bytes(record_path(root))
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
    recovered = integrate_before_planning(config, WorkspaceScope(root), RebaseState())
    assert recovered is not None
    if receipt in {"malformed", "missing_sha"}:
        assert recovered.recovery_record_retained
        assert DEFAULT_FILE_BACKEND.read_bytes(record_path(root)) == original
        direct = recover_incomplete_integration(WorkspaceScope(root), config=config)
        assert direct is not None and direct.recovery_record_retained
        assert _git(root, "rev-parse", target).stdout.strip() == base
        assert not inspect_integration_resolution(root, recovered).dispatch_allowed
        if receipt == "malformed":
            with pytest.raises(RecordUnreadableError):
                clear_record(root)
            assert DEFAULT_FILE_BACKEND.read_bytes(record_path(root)) == original
            assert not inspect_integration_resolution(
                root, RebaseState(recovery_record_retained=True)
            ).dispatch_allowed
    else:
        assert recovered.fast_forwarded
        assert read_record(root) is None
        assert _git(root, "rev-parse", target).stdout.strip() == feature
        assert inspect_integration_resolution(root, recovered).dispatch_allowed
        clear_record(root)
        assert inspect_integration_resolution(
            root, RebaseState(recovery_record_retained=True)
        ).dispatch_allowed
