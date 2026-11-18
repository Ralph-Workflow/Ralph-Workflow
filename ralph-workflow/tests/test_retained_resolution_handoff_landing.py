from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.cli.commands.commit import CommitChainConfig, CommitPlumbingOptions, commit_plumbing
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
)
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.plumbing.commit_plumbing import run_commit_plumbing
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._resolver_controller import make_resolver_controller
from tests._retained_resolution_handoff_support import (
    _blob,
    _clone_template,
    _sha,
    _stage,
    _write_tree,
)
from tests.scenarios._integration_scenario_fixtures import (
    cached_repo,
    close_cached_repo,
    diverged_repo_template,
    interrupted_resolution_repo,
    retained_resolution_config,
)
from tests.test_cli_commit_command import _write_commit_message_doc
from tests.test_pending_merge_commit_recovery import _commit, _git
from tests.test_retained_resolution_handoff import _patch_git_spawn

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.mcp.server.lifecycle import SessionBridgeLike
    from ralph.pipeline.conflict_resolution import RebaseStop
    from ralph.prompts.commit_evidence import CommitEvidenceSource

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

__all__ = ["_patch_git_spawn"]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    'assert _git(root, "switch", "-c", "feature").returncode == 0': (
        "the same feature creation and switches to target and back to feature remain asserted "
        "once while constructing the immutable shared diverged template instead of per run"
    ),
    'assert _git(root, "switch", target).returncode == 0': (
        "the switch to target remains asserted once while constructing the immutable "
        "shared diverged template instead of per run"
    ),
    'assert _git(root, "switch", "feature").returncode == 0': (
        "the switch back to feature remains asserted once while constructing the "
        "immutable shared diverged template instead of per run"
    ),
    "test_owned_landing_reintegrates_moved_target_without_releasing_dispatch": (
        "consolidated the parameter cases into a single run verifying both declining resolver "
        "retention and subsequent recovery when receipt flags are lost before successful resolution"
    ),
    "test_standalone_commit_recovers_ownership_before_commit_session": (
        "removed the unreachable changed_head receipt branch; the parametrization only ever "
        "produced integrated, malformed and missing_target receipts, so that elif and its "
        "trailing else assertions never executed"
    ),
    'assert _git(owner, "switch", "-c", "remote-seed").returncode == 0': (
        "foreign reconciliation repository setup reuses the shared diverged template's "
        "feature branch to seed origin once instead of asserting a separate remote-seed branch"
    ),
    'assert _git(owner, "init", "--bare", str(origin)).returncode == 0': (
        "origin bare repository creation is asserted once while constructing the shared "
        "foreign reconciliation template instead of per test run"
    ),
    'assert _git(owner, "remote", "add", "origin", str(origin)).returncode == 0': (
        "origin remote configuration is asserted once while constructing the shared "
        "foreign reconciliation template instead of per test run"
    ),
    'assert _git(owner, "push", "origin", f"remote-seed:{target}").returncode == 0': (
        "origin target seeding is asserted once while constructing the shared foreign "
        "reconciliation template using the diverged template's feature branch"
    ),
    'assert _git(owner, "switch", target).returncode == 0': (
        "switching to target is asserted once while constructing the shared foreign "
        "reconciliation template instead of per run"
    ),
    "completed owned and landing setups use shared immutable templates": (
        "the same merge or rebase completion, receipt fields, staged resolution, and blob "
        "contents remain asserted; branch construction and the operation itself run once while "
        "building the shared template instead of once per parametrized case"
    ),
}


class _EmptyCommitEvidenceSource:
    def list_changed_paths(self, _repo_root: Path) -> list[str]:
        return []

    def commit_generation_diff(self, _repo_root: Path) -> str:
        return ""


_EMPTY_COMMIT_EVIDENCE_SOURCE: CommitEvidenceSource = _EmptyCommitEvidenceSource()

_COMPLETED_OWNED_CACHE: dict[str, tuple[Path, str, str]] = {}
_COMPLETED_LANDING_CACHE: dict[str, tuple[Path, str, str, str]] = {}


def _completed_owned_template(operation: str, *, reintegrate: bool) -> tuple[Path, str, str]:
    key = f"{operation}:{reintegrate}"
    if key not in _COMPLETED_OWNED_CACHE:
        from git import Repo

        root = Path(tempfile.mkdtemp(prefix=f"it-completed-owned-{operation}-"))
        repo = Repo.init(root, initial_branch="main")
        with repo.config_writer() as cfg:
            cfg.set_value("user", "name", "Test User")
            cfg.set_value("user", "email", "test@example.com")
            cfg.set_value("core", "fsync", "none")
            cfg.set_value("gc", "auto", "0")
            cfg.set_value("core", "logAllRefUpdates", "true")
            cfg.set_value("commit", "gpgSign", "false")
        (root / "README.md").write_text("test", encoding="utf-8")
        repo.index.add(["README.md"])
        repo.index.commit("initial commit")
        repo.close()
        target = "main"
        _commit(root, "base.txt", "base\n")
        assert _git(root, "switch", "-c", "feature").returncode == 0
        feature = _commit(root, "feature.txt", "feature\n")
        assert _git(root, "switch", target).returncode == 0
        moved = _commit(root, "target.txt", "target\n")
        assert _git(root, "switch", "feature").returncode == 0
        write_record(
            root,
            IntegrationRecord(
                phase="integrating",
                target=target,
                pre_feature_sha=feature,
                pre_target_sha=moved,
                reintegrate_pending=reintegrate,
            ),
        )
        assert _git(root, operation, target).returncode == 0
        completed = _sha(root)
        close_cached_repo(root)
        _COMPLETED_OWNED_CACHE[key] = (root, target, completed)
    return _COMPLETED_OWNED_CACHE[key]


def _completed_landing_template(operation: str, phase: str) -> tuple[Path, str, str, str]:
    key = f"{operation}:{phase}"
    if key not in _COMPLETED_LANDING_CACHE:
        root = Path(tempfile.mkdtemp(prefix=f"it-completed-landing-{key}-")) / "repo"
        target, feature, main = interrupted_resolution_repo(root, operation)
        pre_target = cached_repo(root).commit("main~1").hexsha if phase == "integrated" else main
        write_record(
            root,
            IntegrationRecord(
                phase=phase,
                target=target,
                pre_feature_sha=feature,
                pre_target_sha=pre_target,
                integrated_feature_sha=feature if phase == "integrated" else None,
            ),
        )
        (root / "shared.txt").write_text("valuable manual resolution\n", encoding="utf-8")
        _stage(root, "shared.txt")
        index_before = _write_tree(root)
        head_before = _sha(root)
        close_cached_repo(root)
        _COMPLETED_LANDING_CACHE[key] = (root, target, index_before, head_before)
    return _COMPLETED_LANDING_CACHE[key]


def test_owned_landing_reintegrates_moved_target_without_releasing_dispatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo-owned-landing"
    diverged, target, feature, _main = diverged_repo_template()
    _clone_template(diverged, root)
    base = cached_repo(root).commit("main~1").hexsha
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
    config = retained_resolution_config(target, enabled=True)

    controller = make_resolver_controller(FakeClock())
    declined = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=lambda _root, _target, _stop: False,
        conflict_resolver=lambda _root, _target: False,
        recovery_controller=controller,
    )
    assert declined is not None and not declined.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed

    interrupted = read_record(root)
    assert interrupted is not None
    write_record(
        root,
        interrupted.model_copy(
            update={
                "resolving_rebase": False,
                "resolving_paths": (),
            }
        ),
    )

    def resolve(repo: Path, _branch: str, _stop: RebaseStop) -> bool:
        assert read_record(root) is not None
        assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
        (repo / "shared.txt").write_text("feature and target preserved\n", encoding="utf-8")
        return True

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=resolve,
        recovery_controller=controller,
    )
    assert outcome is not None and outcome.fast_forwarded
    assert read_record(root) is None
    assert _sha(root, target) == _sha(root)
    assert _blob(root, target, "shared.txt") == "feature and target preserved\n"


@pytest.mark.parametrize("receipt", ["integrated", "malformed", "missing_target"])
def test_standalone_commit_recovers_ownership_before_commit_session(
    tmp_git_repo: Path,
    receipt: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "base.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature\n")
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
    if receipt == "malformed":
        ownership = root / ".agent" / "auto_integrate_in_progress.json"
        for payload in (
            b'{"unsupported":' + b"9" * 5000 + b"}",
            b'{"unsupported":' + b"[" * 100000 + b"0" + b"]" * 100000 + b"}",
            b"\xff\xfe",
        ):
            ownership.write_bytes(payload)
            assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
            assert ownership.read_bytes() == payload
    elif receipt == "missing_target":
        assert _git(root, "branch", "-D", target).returncode == 0
    head_before = _git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "pending.txt").write_text("keep pending work\n", encoding="utf-8")
    config = retained_resolution_config(target, enabled=False)
    context = make_display_context()
    agent = AgentConfig(cmd="fake-commit", json_parser="generic")
    registry = AgentRegistry.from_config(config)
    registry.register("fake-commit", agent)

    def invoke(_agent: AgentConfig, _prompt: str, *, options: InvokeOptions) -> Iterator[str]:
        assert _sha(root, target) == feature
        _write_commit_message_doc(root, "fix: preserved pending work")
        return iter(())

    monkeypatch.setattr("ralph.cli.commands.commit.invoke_agent", invoke)
    result = run_commit_plumbing(
        diff="pending work",
        repo_root=root,
        chain_config=CommitChainConfig(
            registry=registry,
            agents=["fake-commit"],
            verbose=False,
            agents_policy=load_agents_policy_for_workspace_scope(WorkspaceScope(root), config),
            general_config=config,
        ),
        display_context=context,
        pipeline_deps=make_test_pipeline_deps(context),
        commit_evidence_source=_EMPTY_COMMIT_EVIDENCE_SOURCE,
    )
    if receipt == "integrated":
        assert result.message == "fix: preserved pending work"
        assert read_record(root) is None
        assert _sha(root, target) == feature
    elif receipt == "malformed":
        assert result.failure_details and "integration" in result.failure_details[0]
        assert (root / ".agent" / "auto_integrate_in_progress.json").read_bytes() == b"\xff\xfe"
        assert _sha(root, target) == base
    elif receipt == "missing_target":
        assert not result.message and result.failure_details
        assert read_record(root) is not None
        assert _git(root, "rev-parse", "--verify", target).returncode != 0
    assert _sha(root) == head_before
    assert (root / "pending.txt").read_text(encoding="utf-8") == "keep pending work\n"


@pytest.mark.parametrize(
    ("operation", "reintegrate"),
    [("merge", False), ("rebase", True)],
)
def test_completed_owned_reintegration_lands_after_receipt_write_interruption(
    tmp_path: Path,
    operation: str,
    *,
    reintegrate: bool,
) -> None:
    template, target, completed = _completed_owned_template(operation, reintegrate=reintegrate)
    root = _clone_template(template, tmp_path / f"owned-{operation}")
    config = retained_resolution_config(target, enabled=False)

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        recovery_controller=make_resolver_controller(FakeClock()),
    )

    assert outcome is not None and outcome.fast_forwarded
    assert read_record(root) is None
    assert inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    assert _sha(root, target) == completed
    assert _blob(root, target, "feature.txt") == "feature\n"
    assert _blob(root, target, "target.txt") == "target\n"


@pytest.mark.parametrize(
    ("operation", "phase"),
    [("merge", "integrating"), ("rebase", "integrated")],
)
def test_completed_landing_receipt_preserves_later_operator_resolution(
    tmp_path: Path,
    operation: str,
    phase: str,
) -> None:
    template, target, index_before, head_before = _completed_landing_template(operation, phase)
    root = _clone_template(template, tmp_path / f"landing-{operation}-{phase}")
    config = retained_resolution_config(target, enabled=False)

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        recovery_controller=make_resolver_controller(FakeClock()),
    )

    assert outcome is not None and not outcome.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    assert (root / "shared.txt").read_text(encoding="utf-8") == "valuable manual resolution\n"
    assert _write_tree(root) == index_before
    assert _sha(root) == head_before


def test_commit_cli_reports_corrupt_ownership_without_deleting_evidence(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    assert "fix: retained artifact" in (
        root / ".agent" / "artifacts" / "commit_message.md"
    ).read_text(encoding="utf-8")


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
        write_record(
            root,
            IntegrationRecord(
                phase="integrated",
                target="missing-target",
                pre_feature_sha=base,
                pre_target_sha=base,
                integrated_feature_sha=base,
            ),
        )
        return Bridge()

    result = run_commit_plumbing(
        diff="pending work",
        repo_root=root,
        chain_config=CommitChainConfig(
            registry=registry,
            agents=["fake-commit"],
            verbose=False,
            agents_policy=load_agents_policy_for_workspace_scope(WorkspaceScope(root), config),
            general_config=config,
        ),
        display_context=context,
        pipeline_deps=make_test_pipeline_deps(context, bridge_factory=race),
        commit_evidence_source=_EMPTY_COMMIT_EVIDENCE_SOURCE,
    )
    assert not result.message and result.failure_details
    assert "integration" in result.failure_details[0]
    assert read_record(root) is not None
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == base


_FOREIGN_TEMPLATE_CACHE: list[tuple[Path, str]] = []


def _foreign_reconcile_template(root: Path | None = None) -> tuple[Path, str]:
    if not _FOREIGN_TEMPLATE_CACHE:
        template_base = Path(tempfile.mkdtemp(prefix="it-foreign-reconcile-"))
        diverged, target, _, _ = diverged_repo_template(root)
        tmpl_owner = template_base / "owner"
        _clone_template(diverged, tmpl_owner)
        assert _git(tmpl_owner, "config", "--replace-all", "core.fsync", "none").returncode == 0
        assert _git(tmpl_owner, "config", "--replace-all", "gc.auto", "0").returncode == 0
        tmpl_origin = template_base / "origin.git"
        assert _git(tmpl_owner, "init", "--bare", str(tmpl_origin)).returncode == 0
        assert _git(tmpl_owner, "remote", "add", "origin", str(tmpl_origin)).returncode == 0
        assert _git(tmpl_owner, "push", "origin", f"feature:{target}").returncode == 0
        assert _git(tmpl_owner, "switch", target).returncode == 0
        assert _git(tmpl_owner, "branch", "-D", "feature").returncode == 0
        close_cached_repo(tmpl_owner)
        _FOREIGN_TEMPLATE_CACHE.append((tmpl_owner, target))
    return _FOREIGN_TEMPLATE_CACHE[0]


@pytest.mark.timeout_seconds(20)
def test_foreign_reconciliation_completion_retains_initiating_feature_landing(
    tmp_path: Path,
) -> None:
    tmpl_owner, target = _foreign_reconcile_template()
    owner = _clone_template(tmpl_owner, tmp_path / "owner")
    feature = tmp_path / "feature"
    assert _git(owner, "worktree", "add", "-b", "feature", str(feature)).returncode == 0
    _commit(feature, "feature.txt", "valuable completed feature\n")
    controller = make_resolver_controller(FakeClock())
    initial = auto_integrate_after_commit(
        UnifiedConfig.model_validate({"general": {"auto_integrate_target": target}}),
        WorkspaceScope(feature),
        RebaseState(),
        rebase_stop_resolver=lambda *_args: False,
        sleep=lambda _s: None,
        jitter=lambda: 0.0,
        recovery_controller=controller,
    )
    assert initial is not None and read_record(feature) is not None

    def resolve(root: Path, _branch: str, _stop: RebaseStop) -> bool:
        (root / "shared.txt").write_text("local and remote preserved\n", encoding="utf-8")
        return True

    config = retained_resolution_config(target, enabled=False)
    from ralph.pipeline.auto_integrate_record import record_path

    owner_receipt = record_path(owner)
    owner_receipt.parent.mkdir(parents=True, exist_ok=True)
    owner_receipt.write_text("{", encoding="utf-8")
    before = _sha(owner)
    blocked = recover_incomplete_integration(
        WorkspaceScope(feature),
        config=config,
        rebase_stop_resolver=resolve,
        recovery_controller=controller,
    )
    assert blocked is not None and blocked.recovery_record_retained
    assert _sha(owner) == before
    assert owner_receipt.read_text(encoding="utf-8") == "{"
    assert not inspect_integration_resolution(feature, blocked).dispatch_allowed
    owner_receipt.unlink()
    recovered = recover_incomplete_integration(
        WorkspaceScope(feature),
        config=config,
        rebase_stop_resolver=resolve,
        recovery_controller=controller,
    )
    assert recovered is not None
    assert not inspect_integration_resolution(feature, recovered).dispatch_allowed
    assert read_record(feature) is not None
    landed = recover_incomplete_integration(
        WorkspaceScope(feature),
        config=config,
        rebase_stop_resolver=resolve,
        recovery_controller=controller,
    )
    assert landed is not None and landed.fast_forwarded
    assert _sha(feature) == _sha(feature, target)
    assert _blob(feature, target, "feature.txt") == "valuable completed feature\n"
    assert read_record(feature) is None


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_aborted_operation_reset_to_target_cannot_be_misreported_as_completed(
    tmp_path: Path, operation: str
) -> None:
    root = tmp_path / f"case-{operation}"
    target, feature, main = interrupted_resolution_repo(root, operation)
    assert read_record(root) is not None
    assert _git(root, operation, "--abort").returncode == 0
    controller = make_resolver_controller(FakeClock())
    aborted = recover_incomplete_integration(
        WorkspaceScope(root),
        recovery_controller=controller,
    )
    assert aborted is not None and aborted.recovery_record_retained
    assert read_record(root) is not None
    assert _git(root, "reset", "--hard", main).returncode == 0
    outcome = integrate_before_planning(
        retained_resolution_config(target),
        WorkspaceScope(root),
        RebaseState(),
        sleep=lambda _s: None,
        jitter=lambda: 0.0,
        recovery_controller=controller,
    )
    assert outcome is not None and not outcome.fast_forwarded
    assert outcome.recovery_record_retained
    assert _sha(root, target) == main
    assert _git(root, "cat-file", "-e", feature).returncode == 0
