from __future__ import annotations

import dataclasses
import functools
import inspect
import tempfile
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import RebaseOutcomeKind
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
)
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.auto_integrate_resolve import mark_pending_merge
from ralph.pipeline.conflict_resolution.rebase_loop import prepare_pending_rebase
from ralph.pipeline.integration_diagnostic import repair_pending_merge
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._resolver_controller import make_resolver_controller
from tests._retained_resolution_handoff_helpers import (
    _blob,
    _clone_template,
    _sha,
    install_fast_git_spawn,
)
from tests.scenarios._integration_scenario_fixtures import (
    close_cached_repo,
    diverged_repo_template,
    interrupted_resolution_repo,
    retained_resolution_config,
)
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop
    from ralph.policy.models import PolicyBundle

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "retained-resolution git assertions use equivalent GitPython object reads": (
        "the assertions still compare the same branch and HEAD object ids and the same "
        "committed blob contents; only their read-only git transport changed from the git "
        "executable to GitPython"
    ),
    "test_completed_retained_rebase_lands_or_replays_stale_intent": (
        "removed the unreachable empty completion conditional; the parametrization only ever "
        "produced prepared and unrelated completions"
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
        "reconciliation template instead of per test run"
    ),
    "autouse real-git spawn bypasses process-manager bookkeeping": (
        "every git argv, cwd, env, timeout, capture, and returncode observable is unchanged; "
        "only the public route_run_git seam drops ManagedProcess accounting while still "
        "executing the same git binary via subprocess.run"
    ),
    "test_stale_resolution_replay_clears_intent_and_retries_auto_rebase": (
        "the same replay observables remain: cleared resolving flags, snapshot then record "
        "ordering, and reintegrate_pending handoff; the private _has_user_work and "
        "_move_feature_to_snapshot patches were replaced by public run_git seams returning "
        "clean porcelain status and successful ref updates"
    ),
}


@pytest.fixture(autouse=True)
def _patch_git_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fast_git_spawn(monkeypatch)


@functools.lru_cache(maxsize=1)
def _default_policy() -> PolicyBundle:
    return load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")


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


@pytest.mark.parametrize("completion", ["prepared", "unrelated"])
def test_completed_retained_rebase_lands_or_replays_stale_intent(
    tmp_path: Path, completion: str
) -> None:
    root = tmp_path / f"repo-{completion}"
    target, _, _ = interrupted_resolution_repo(root, "rebase")
    content = "resolved feature and main\n"
    (root / "shared.txt").write_text(content, encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    if completion == "prepared":
        prepare_pending_rebase(root)
    assert _git(root, "-c", "core.editor=true", "rebase", "--continue").returncode == 0
    if completion == "unrelated":
        _commit(root, "unrelated.txt", "later commit\n")
    controller = make_resolver_controller(FakeClock())
    outcome = integrate_before_planning(
        retained_resolution_config(target),
        WorkspaceScope(root),
        RebaseState(),
        sleep=lambda _s: None,
        jitter=lambda: 0.0,
        recovery_controller=controller,
    )
    assert outcome is not None
    if completion == "unrelated":
        assert outcome.fast_forwarded
        assert read_record(root) is None
        backup_refs = _git(
            root,
            "for-each-ref",
            "--format=%(refname)",
            "refs/ralph-backup/",
        ).stdout.split()
        assert backup_refs
        return
    assert outcome.fast_forwarded
    assert _sha(root, target) == _sha(root)
    assert read_record(root) is None


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


@pytest.mark.parametrize(
    ("legacy", "operation"),
    [(False, "merge"), (True, "rebase")],
)
def test_marker_free_interrupted_resolution_rechecks_and_stages_completed_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy: bool,
    operation: str,
) -> None:
    root = tmp_path / f"repo-{legacy}-{operation}"
    target, _, _ = interrupted_resolution_repo(root, operation)
    if not legacy and operation == "merge":
        mark_pending_merge(root, target, resolving=True)
    (root / "shared.txt").write_text("staged partial\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    (root / "shared.txt").write_text("valuable unstaged completion\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("unfinished user work\n", encoding="utf-8")
    from rich.console import Console

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_agent import (
        build_agent_conflict_resolver,
        build_agent_rebase_stop_resolver,
    )
    from ralph.pipeline.conflict_resolution.session import resolution_chain_agents
    from ralph.pipeline.events import PipelineEvent

    calls: list[str] = []

    def execute(*_args: object, **_kwargs: object) -> PipelineEvent:
        calls.append("agent")
        assert (root / "shared.txt").read_text() == "valuable unstaged completion\n"
        prompt = root / ".agent" / "tmp" / "rebase_conflict_resolution_prompt.md"
        assert "shared.txt" in prompt.read_text()
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr("ralph.pipeline.effect_executor.execute_agent_effect", execute)
    config = retained_resolution_config(target)
    policy = _default_policy()
    registry = AgentRegistry()
    registry.agents.update(
        {name: AgentConfig(cmd="fake") for name in resolution_chain_agents(policy)}
    )
    context = make_display_context(console=Console(file=StringIO()))
    deps = make_test_pipeline_deps(context)
    deps = dataclasses.replace(deps, recovery_controller=make_resolver_controller(FakeClock()))
    scope = WorkspaceScope(root)
    display = ParallelDisplay(context)
    resolver_kw = {
        "config": config,
        "pipeline_deps": deps,
        "workspace_scope": scope,
        "policy_bundle": policy,
        "registry": registry,
        "display": display,
        "display_context": context,
    }
    merge_resolver = build_agent_conflict_resolver(**resolver_kw)
    rebase_resolver = build_agent_rebase_stop_resolver(**resolver_kw)
    if legacy:
        from ralph.pipeline.parallel.worker_runtime import run_worker_auto_integration

        outcome = run_worker_auto_integration(
            config=config,
            workspace_scope=scope,
            policy_bundle=policy,
            registry=registry,
            pipeline_deps=deps,
            display_context=context,
            recover_first=True,
            state=RebaseState(last_action="conflict", recovery_record_retained=True),
        )
    else:
        outcome = integrate_before_planning(
            config,
            scope,
            RebaseState(),
            conflict_resolver=merge_resolver,
            rebase_stop_resolver=rebase_resolver,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
            recovery_controller=deps.recovery_controller,
        )
    assert calls == ["agent"]
    assert outcome is not None and outcome.fast_forwarded
    assert _blob(root, target, "shared.txt") == "valuable unstaged completion\n"
    assert _git(root, "cat-file", "-e", f"{target}:unrelated.txt").returncode != 0
    assert (root / "unrelated.txt").read_text() == "unfinished user work\n"
    assert read_record(root) is None


def test_foreign_target_commit_repair_uses_git_owner_and_keeps_record_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rich.console import Console

    from ralph.pipeline.auto_integrate_record import clear_record
    from ralph.pipeline.events import PipelineEvent
    from ralph.policy.models import PolicyBundle

    owner = tmp_path / "owner"
    target, _, _ = interrupted_resolution_repo(owner, "rebase")
    feature = tmp_path / "record-owner"
    assert (
        _git(owner, "worktree", "add", "-b", "record-owner", str(feature), target).returncode == 0
    )
    original_feature = _git(feature, "rev-parse", "HEAD").stdout
    (owner / "shared.txt").write_text("resolved but invalid\n", encoding="utf-8")
    assert _git(owner, "add", "shared.txt").returncode == 0
    prepare_pending_rebase(owner)
    record = read_record(owner)
    assert record is not None and record.rebase_continue_pending
    write_record(
        feature,
        record.model_copy(
            update={
                "target": "feature",
                "operation_kind": "target_reconcile",
                "owning_worktree": str(owner),
            }
        ),
    )
    clear_record(owner)
    seen: list[Path] = []

    def execute(
        _effect: object,
        _config: object,
        _deps: object,
        scope: WorkspaceScope,
        **_kwargs: object,
    ) -> PipelineEvent:
        seen.append(scope.root)
        assert scope.root == owner
        (owner / "shared.txt").write_text("fixed\n", encoding="utf-8")
        (owner / ".agent" / "tmp" / "pending_commit_repair_paths.json").write_text('["shared.txt"]')
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr("ralph.pipeline.effect_executor.execute_agent_effect", execute)
    controller = make_resolver_controller(FakeClock())
    deps = make_test_pipeline_deps(make_display_context(console=Console(file=StringIO())))
    deps = dataclasses.replace(deps, recovery_controller=controller)
    assert repair_pending_merge(
        controller=controller,
        workspace_scope=WorkspaceScope(feature),
        config=UnifiedConfig(),
        pipeline_deps=deps,
        policy_bundle=PolicyBundle.model_construct(),
        display=None,
        display_context=None,
        agents=("repair",),
        failure="source validation failed",
    )
    assert seen == [owner]
    assert read_record(owner) is None
    assert read_record(feature) is not None
    result = recover_incomplete_integration(
        WorkspaceScope(feature),
        recovery_controller=controller,
    )
    assert result is not None and result.recovery_record_retained
    assert _blob(owner, "feature", "shared.txt") == "fixed\n"
    assert _sha(feature) == original_feature.strip()
    assert read_record(feature) is not None


def test_stale_resolution_modules_do_not_use_legacy_decider_or_terminal_proof_text() -> None:
    import importlib

    _integration_continuation = importlib.import_module("ralph.pipeline._integration_continuation")
    _owned_landing_reintegration = importlib.import_module(
        "ralph.pipeline._owned_landing_reintegration"
    )
    from ralph.pipeline import auto_integrate_recovery_record

    for module in (
        _integration_continuation,
        _owned_landing_reintegration,
        auto_integrate_recovery_record,
    ):
        source = inspect.getsource(module)
        assert "next_integration_action" not in source
        assert "lacks verified completion proof" not in source


def test_stale_resolution_replay_clears_intent_and_retries_auto_rebase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import importlib

    from ralph.git.merge import UnmergedPathsObservation
    from ralph.pipeline.integration_observation import InProgressKind, Reading

    owned = importlib.import_module("ralph.pipeline._owned_landing_reintegration")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        resolving_rebase=True,
    )
    written: list[IntegrationRecord] = []
    replayed: list[IntegrationRecord] = []
    sequence: list[str] = []
    head_sha = {"value": "a" * 40}

    def fake_snapshot(*_args: object, **_kwargs: object) -> tuple[str, str]:
        sequence.append("backup_ref")
        return ("refs/ralph-backup/feature-abcdef12", "c" * 40)

    def fake_write(_root: Path, rec: IntegrationRecord) -> None:
        sequence.append("record")
        written.append(rec)

    def fake_run_git(args: object, **_kwargs: object) -> GitRunResult:
        argv = tuple(args) if isinstance(args, (list, tuple)) else (str(args),)
        if argv and argv[0] == "reset" and "--hard" in argv:
            head_sha["value"] = str(argv[-1])
        return GitRunResult(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        owned, "read_in_progress_kind", lambda _root: Reading.known(InProgressKind.NONE)
    )
    monkeypatch.setattr(
        owned,
        "observe_unmerged_paths",
        lambda _root: UnmergedPathsObservation(paths=(), query_ok=True),
    )
    monkeypatch.setattr(owned, "run_git", fake_run_git)
    monkeypatch.setattr(owned, "branch_sha", lambda _root, _target: "b" * 40)
    monkeypatch.setattr(owned, "ancestry_state", lambda *_args: False)
    monkeypatch.setattr(owned, "get_head_sha", lambda _root: head_sha["value"])
    monkeypatch.setattr(owned, "current_branch_or_detached_marker", lambda _root: "feature")
    monkeypatch.setattr(
        owned,
        "create_stale_resolution_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(owned, "write_record", fake_write)

    def fake_reintegrate(
        _root: Path,
        replay_record: IntegrationRecord,
        _config: UnifiedConfig,
        *_args: object,
    ) -> RebaseState:
        replayed.append(replay_record)
        return RebaseState(last_outcome=RebaseOutcomeKind.REBASED, last_target="main")

    monkeypatch.setattr(owned, "reintegrate_owned_landing", fake_reintegrate)

    outcome = owned.replay_stale_resolution(tmp_path, record, None, None, None)

    assert outcome.last_outcome is RebaseOutcomeKind.REBASED
    assert "lacks verified completion proof" not in (outcome.last_reason or "")
    assert written
    assert written[0].resolving_rebase is False
    assert written[0].resolving_merge is False
    assert written[0].pre_feature_sha == "c" * 40
    assert sequence == ["backup_ref", "record"]
    assert replayed and replayed[0].resolving_rebase is False
    assert replayed[0].reintegrate_pending is True
