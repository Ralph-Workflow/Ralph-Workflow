from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.cli.commands.commit import CommitPlumbingOptions, commit_plumbing
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.plumbing.commit_integration import prepare_commit_integration
from ralph.pipeline.plumbing.commit_plumbing import run_commit_plumbing
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_cli_commit_command import _write_commit_message_doc
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.mcp.server.lifecycle import SessionBridgeLike
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("receipt_flags_lost", [False, True])
def test_owned_landing_reintegrates_moved_target_without_releasing_dispatch(
    tmp_git_repo: Path, *, enabled: bool, receipt_flags_lost: bool,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    write_record(root, IntegrationRecord(
        phase="integrated", target=target, pre_feature_sha=feature,
        pre_target_sha=base, integrated_feature_sha=feature,
    ))
    assert _git(root, "switch", target).returncode == 0
    _commit(root, "shared.txt", "target\n")
    assert _git(root, "switch", "feature").returncode == 0
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_enabled": enabled, "auto_integrate_target": target,
        "auto_integrate_remote_enabled": False,
    }})

    declined = recover_incomplete_integration(
        WorkspaceScope(root), config=config,
        rebase_stop_resolver=lambda _root, _target, _stop: False,
        conflict_resolver=lambda _root, _target: False,
    )
    assert declined is not None and not declined.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed

    if receipt_flags_lost:
        interrupted = read_record(root)
        assert interrupted is not None
        write_record(root, interrupted.model_copy(update={
            "resolving_rebase": False, "resolving_paths": (),
        }))

    def resolve(repo: Path, _branch: str, _stop: RebaseStop) -> bool:
        assert read_record(root) is not None
        assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
        (repo / "shared.txt").write_text("feature and target preserved\n", encoding="utf-8")
        return True

    outcome = recover_incomplete_integration(
        WorkspaceScope(root), config=config, rebase_stop_resolver=resolve,
    )
    assert outcome is not None and outcome.fast_forwarded
    assert read_record(root) is None
    assert _git(root, "rev-parse", target).stdout == _git(root, "rev-parse", "HEAD").stdout
    assert _git(root, "show", f"{target}:shared.txt").stdout == "feature and target preserved\n"


@pytest.mark.parametrize("receipt", ["integrated", "malformed", "missing_target", "changed_head"])
def test_standalone_commit_recovers_ownership_before_commit_session(
    tmp_git_repo: Path, receipt: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "base.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature\n")
    write_record(root, IntegrationRecord(
        phase="integrated", target=target, pre_feature_sha=feature,
        pre_target_sha=base, integrated_feature_sha=feature,
    ))
    if receipt == "malformed":
        (root / ".agent" / "auto_integrate_in_progress.json").write_text("{", encoding="utf-8")
    elif receipt == "missing_target":
        assert _git(root, "branch", "-D", target).returncode == 0
    elif receipt == "changed_head":
        _commit(root, "operator.txt", "operator work\n")
    head_before = _git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "pending.txt").write_text("keep pending work\n", encoding="utf-8")
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_enabled": False, "auto_integrate_target": target,
        "auto_integrate_remote_enabled": False,
    }})
    context = make_display_context()
    agent = AgentConfig(cmd="fake-commit", json_parser="generic")
    registry = AgentRegistry.from_config(config)
    registry.register("fake-commit", agent)

    def invoke(_agent: AgentConfig, _prompt: str, *, options: InvokeOptions) -> Iterator[str]:
        assert _git(root, "rev-parse", target).stdout.strip() == feature
        _write_commit_message_doc(root, "fix: preserved pending work")
        return iter(())

    monkeypatch.setattr("ralph.cli.commands.commit.invoke_agent", invoke)
    result = run_commit_plumbing(
        diff="pending work", repo_root=root,
        chain_config=CommitChainConfig(
            registry=registry, agents=["fake-commit"], verbose=False,
            agents_policy=load_agents_policy_for_workspace_scope(WorkspaceScope(root), config),
            general_config=config,
        ),
        display_context=context, pipeline_deps=make_test_pipeline_deps(context),
    )
    if receipt == "integrated":
        assert result.message == "fix: preserved pending work"
        assert read_record(root) is None
        assert _git(root, "rev-parse", target).stdout.strip() == feature
    elif receipt == "malformed":
        assert result.failure_details and "integration" in result.failure_details[0]
        assert (root / ".agent" / "auto_integrate_in_progress.json").read_text(encoding="utf-8") == "{"
        assert _git(root, "rev-parse", target).stdout.strip() == base
    elif receipt == "missing_target":
        assert not result.message and result.failure_details
        assert read_record(root) is not None
        assert _git(root, "rev-parse", "--verify", target).returncode != 0
    else:
        assert not result.message and result.failure_details
        assert read_record(root) is not None
        assert _git(root, "rev-parse", target).stdout.strip() == base
        assert _git(root, "show", "HEAD:operator.txt").stdout == "operator work\n"
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == head_before
    assert (root / "pending.txt").read_text(encoding="utf-8") == "keep pending work\n"


@pytest.mark.parametrize("operation", ["merge", "rebase"])
@pytest.mark.parametrize("reintegrate", [False, True])
def test_completed_owned_reintegration_lands_after_receipt_write_interruption(
    tmp_git_repo: Path, operation: str, *, reintegrate: bool,
) -> None:
    root = tmp_git_repo
    assert _git(root, "config", "--replace-all", "core.logAllRefUpdates", "true").returncode == 0
    target = _git(root, "branch", "--show-current").stdout.strip()
    _commit(root, "base.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature\n")
    assert _git(root, "switch", target).returncode == 0
    moved = _commit(root, "target.txt", "target\n")
    assert _git(root, "switch", "feature").returncode == 0
    write_record(root, IntegrationRecord(
        phase="integrating", target=target, pre_feature_sha=feature,
        pre_target_sha=moved, reintegrate_pending=reintegrate,
    ))
    assert _git(root, operation, target).returncode == 0
    completed = _git(root, "rev-parse", "HEAD").stdout.strip()
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_enabled": False, "auto_integrate_target": target,
        "auto_integrate_remote_enabled": False,
    }})

    outcome = recover_incomplete_integration(WorkspaceScope(root), config=config)

    assert outcome is not None and outcome.fast_forwarded
    assert read_record(root) is None
    assert inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    assert _git(root, "rev-parse", target).stdout.strip() == completed
    assert _git(root, "show", f"{target}:feature.txt").stdout == "feature\n"
    assert _git(root, "show", f"{target}:target.txt").stdout == "target\n"


@pytest.mark.parametrize("operation", ["merge", "rebase"])
@pytest.mark.parametrize("phase", ["integrating", "integrated"])
def test_completed_landing_receipt_preserves_later_operator_resolution(
    tmp_git_repo: Path, operation: str, phase: str,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", target).returncode == 0
    moved = _commit(root, "shared.txt", "target\n")
    assert _git(root, "switch", "feature").returncode == 0
    write_record(root, IntegrationRecord(
        phase=phase, target=target, pre_feature_sha=feature,
        pre_target_sha=base if phase == "integrated" else moved,
        integrated_feature_sha=feature if phase == "integrated" else None,
    ))
    assert _git(root, operation, target).returncode != 0
    (root / "shared.txt").write_text("valuable manual resolution\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    index_before = _git(root, "write-tree").stdout
    head_before = _git(root, "rev-parse", "HEAD").stdout
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_enabled": False, "auto_integrate_target": target,
        "auto_integrate_remote_enabled": False,
    }})

    outcome = recover_incomplete_integration(WorkspaceScope(root), config=config)

    assert outcome is not None and not outcome.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    assert (root / "shared.txt").read_text(encoding="utf-8") == "valuable manual resolution\n"
    assert _git(root, "write-tree").stdout == index_before
    assert _git(root, "rev-parse", "HEAD").stdout == head_before


def test_commit_cli_reports_corrupt_ownership_without_deleting_evidence(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_git_repo
    _commit(root, "base.txt", "base\n")
    receipt = root / ".agent" / "auto_integrate_in_progress.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("{", encoding="utf-8")
    _write_commit_message_doc(root, "fix: retained artifact")
    monkeypatch.chdir(root)
    def load_config(*_args: object, **_kwargs: object) -> UnifiedConfig:
        return UnifiedConfig()

    monkeypatch.setattr("ralph.cli.commands.commit.load_config", load_config)
    result = commit_plumbing(
        options=CommitPlumbingOptions(generate_commit=True),
        display_context=make_display_context(),
    )
    assert result == 1
    assert receipt.read_text(encoding="utf-8") == "{"
    assert "fix: retained artifact" in (root / ".agent" / "artifacts" / "commit_message.md").read_text(encoding="utf-8")


def test_commit_dispatch_race_returns_integration_blocker_not_traceback(tmp_git_repo: Path) -> None:
    root = tmp_git_repo
    base = _commit(root, "base.txt", "base\n")
    config = UnifiedConfig()
    registry = AgentRegistry.from_config(config)
    registry.register("fake-commit", AgentConfig(cmd="fake-commit", json_parser="generic"))
    context = make_display_context()
    class Bridge:
        run_id = "commit-race"

        def start(self) -> None:
            return None

        def shutdown(self) -> None:
            return None

        def endpoint_uri(self) -> str:
            return "http://127.0.0.1:12345/mcp"

        def agent_endpoint_uri(self) -> str:
            return self.endpoint_uri()

    def race(**_kwargs: object) -> SessionBridgeLike:
        write_record(root, IntegrationRecord(
            phase="integrated", target="missing-target", pre_feature_sha=base,
            pre_target_sha=base, integrated_feature_sha=base,
        ))
        return Bridge()

    result = run_commit_plumbing(
        diff="pending work", repo_root=root,
        chain_config=CommitChainConfig(
            registry=registry, agents=["fake-commit"], verbose=False,
            agents_policy=load_agents_policy_for_workspace_scope(WorkspaceScope(root), config),
            general_config=config,
        ),
        display_context=context,
        pipeline_deps=make_test_pipeline_deps(context, bridge_factory=race),
    )
    assert not result.message and result.failure_details
    assert "integration" in result.failure_details[0]
    assert read_record(root) is not None
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == base


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


def test_foreign_reconciliation_completion_retains_initiating_feature_landing(
    tmp_git_repo: Path, tmp_path: Path,
) -> None:
    owner = tmp_git_repo
    target = _git(owner, "branch", "--show-current").stdout.strip()
    _commit(owner, "shared.txt", "base\n")
    assert _git(owner, "switch", "-c", "remote-seed").returncode == 0
    _commit(owner, "shared.txt", "remote\n")
    origin = tmp_path / "origin.git"
    assert _git(owner, "init", "--bare", str(origin)).returncode == 0
    assert _git(owner, "remote", "add", "origin", str(origin)).returncode == 0
    assert _git(owner, "push", "origin", f"remote-seed:{target}").returncode == 0
    assert _git(owner, "switch", target).returncode == 0
    _commit(owner, "shared.txt", "local\n")
    feature = tmp_path / "feature"
    assert _git(owner, "worktree", "add", "-b", "feature", str(feature)).returncode == 0
    _commit(feature, "feature.txt", "valuable completed feature\n")
    initial = auto_integrate_after_commit(
        UnifiedConfig.model_validate({"general": {"auto_integrate_target": target}}),
        WorkspaceScope(feature), RebaseState(), rebase_stop_resolver=lambda *_args: False,
    )
    assert initial is not None and read_record(feature) is not None

    def resolve(root: Path, _branch: str, _stop: RebaseStop) -> bool:
        (root / "shared.txt").write_text("local and remote preserved\n", encoding="utf-8")
        return True

    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_enabled": False, "auto_integrate_target": target,
        "auto_integrate_remote_enabled": False,
    }})
    recovered = recover_incomplete_integration(
        WorkspaceScope(feature), config=config, rebase_stop_resolver=resolve,
    )
    assert recovered is not None
    assert not inspect_integration_resolution(feature, recovered).dispatch_allowed
    assert read_record(feature) is not None
    landed = recover_incomplete_integration(
        WorkspaceScope(feature), config=config, rebase_stop_resolver=resolve,
    )
    assert landed is not None and landed.fast_forwarded
    assert _git(feature, "rev-parse", "HEAD").stdout == _git(feature, "rev-parse", target).stdout
    assert _git(feature, "show", f"{target}:feature.txt").stdout == "valuable completed feature\n"
    assert read_record(feature) is None


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_stale_resolver_receipt_does_not_adopt_replacement_operator_operation(
    tmp_git_repo: Path, operation: str,
) -> None:
    root = tmp_git_repo
    target, feature, _main = _interrupted(root, operation)
    assert _git(root, operation, "--abort").returncode == 0
    assert _git(root, "switch", "-c", "other", f"{feature}^").returncode == 0
    _commit(root, "shared.txt", "other target\n")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, operation, "other").returncode != 0
    (root / "shared.txt").write_text("operator partial resolution\n", encoding="utf-8")
    head_before = _git(root, "rev-parse", "HEAD").stdout

    def resolve(repo: Path, _branch: str) -> bool:
        (repo / "shared.txt").write_text("incorrectly adopted operation\n", encoding="utf-8")
        return True

    outcome = recover_incomplete_integration(
        WorkspaceScope(root), config=UnifiedConfig.model_validate({"general": {
            "auto_integrate_target": target, "auto_integrate_remote_enabled": False,
        }}), conflict_resolver=resolve,
        rebase_stop_resolver=lambda repo, branch, _stop: resolve(repo, branch),
    )

    assert outcome is not None and outcome.recovery_record_retained
    assert not outcome.fast_forwarded
    assert read_record(root) is not None
    assert _git(root, "rev-parse", "HEAD").stdout == head_before
    assert (root / "shared.txt").read_text(encoding="utf-8") == "operator partial resolution\n"
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_aborted_owned_resolution_retains_landing_until_retry_completes(
    tmp_git_repo: Path, operation: str,
) -> None:
    root = tmp_git_repo
    target, feature, _moved = _interrupted(root, operation)
    assert _git(root, operation, "--abort").returncode == 0
    config = UnifiedConfig.model_validate({"general": {
        "auto_integrate_enabled": False, "auto_integrate_target": target,
        "auto_integrate_remote_enabled": False,
    }})

    waiting = recover_incomplete_integration(WorkspaceScope(root))

    assert waiting is not None and waiting.recovery_record_retained
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == feature

    def resolve(repo: Path, _branch: str, _stop: RebaseStop) -> bool:
        (repo / "shared.txt").write_text("feature and main preserved\n", encoding="utf-8")
        return True

    landed = recover_incomplete_integration(
        WorkspaceScope(root), config=config, rebase_stop_resolver=resolve,
    )
    assert landed is not None and landed.fast_forwarded
    assert read_record(root) is None
    assert _git(root, "rev-parse", target).stdout == _git(root, "rev-parse", "HEAD").stdout


@pytest.mark.parametrize("operation", ["merge", "rebase"])
@pytest.mark.parametrize("seam", ["planning", "after_commit", "boundary", "standalone_commit"])
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
        "auto_integrate_enabled": seam != "standalone_commit",
    }})
    outcome: RebaseState | None
    if seam == "standalone_commit":
        context = make_display_context()
        verdict = prepare_commit_integration(
            root, config, make_test_pipeline_deps(context), AgentRegistry.from_config(config),
            context, conflict_resolver=merge_resolver, rebase_stop_resolver=rebase_resolver,
        )
        assert verdict.dispatch_allowed
        outcome = RebaseState(fast_forwarded=True)
    elif seam == "planning":
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
    assert read_record(root) is not None
    assert _git(root, operation, "--abort").returncode == 0
    aborted = recover_incomplete_integration(WorkspaceScope(root))
    assert aborted is not None and aborted.recovery_record_retained
    assert read_record(root) is not None
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
    assert result is not None and result.recovery_record_retained
    assert _git(owner, "show", "feature:shared.txt").stdout == "fixed\n"
    assert _git(feature, "rev-parse", "HEAD").stdout == original_feature
    assert read_record(feature) is not None


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
