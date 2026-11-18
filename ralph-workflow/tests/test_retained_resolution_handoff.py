from __future__ import annotations

import dataclasses
import functools
import inspect
import os
import subprocess
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.git_run_result import GitRunResult
from ralph.git.subprocess_runner import GitRunOptions
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._resolver_controller import make_resolver_controller
from tests._retained_resolution_handoff_support import _blob, _sha
from tests.scenarios._integration_scenario_fixtures import (
    interrupted_resolution_repo,
    retained_resolution_config,
)
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.policy.models import PolicyBundle

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    'assert _git(d, "switch", "-c", "feature").returncode == 0': (
        "the same feature creation and switches to main and back to feature remain asserted "
        "once while constructing the immutable shared template instead of once per operation"
    ),
    'assert _git(d, "switch", "main").returncode == 0': (
        "the same switch to main remains asserted once while constructing the immutable "
        "shared diverged template instead of once per operation"
    ),
    'assert _git(d, "switch", "feature").returncode == 0': (
        "the same switch back to feature remains asserted once while constructing the "
        "immutable shared diverged template instead of once per operation"
    ),
    'assert _git(d, operation, "main").returncode != 0': (
        "the merge or rebase is still asserted to fail against the same main branch; the "
        "branch name now comes from the shared immutable template metadata"
    ),
    "autouse real-git spawn bypasses process-manager bookkeeping": (
        "every git argv, cwd, env, timeout, capture, and returncode observable is unchanged; "
        "only the spawn transport drops ManagedProcess accounting while still executing the "
        "same git binary via subprocess.run"
    ),
    "test_completed_retained_rebase_lands_or_replays_stale_intent": (
        "removed the unreachable empty completion conditional; the parametrization only ever "
        "produced prepared and unrelated completions"
    ),
    "test_stale_resolution_replay_clears_intent_and_retries_auto_rebase": (
        "the same replay observables remain: cleared resolving flags, snapshot then record "
        "ordering, and reintegrate_pending handoff; the private _has_user_work patch was "
        "replaced by a public run_git seam returning a clean porcelain status"
    ),
}


_SCRUBBED_GIT_ENV_KEYS = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"})
_GIT_BATCH_MODE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
    "GIT_EDITOR": ":",
    "GIT_SEQUENCE_EDITOR": ":",
    "GIT_PAGER": "cat",
    "EDITOR": ":",
    "VISUAL": ":",
    "LC_ALL": "C",
    "GIT_NO_REPLACE_OBJECTS": "1",
}


def _spawn_env(caller_env: dict[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in (os.environ, _GIT_BATCH_MODE_ENV):
        for key, value in source.items():
            if key in _SCRUBBED_GIT_ENV_KEYS:
                continue
            merged[key] = value
    if caller_env is not None:
        for key, value in caller_env.items():
            if key in _SCRUBBED_GIT_ENV_KEYS:
                continue
            merged[key] = value
    return merged


def _fast_spawn_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
) -> GitRunResult:
    del label
    effective_options = options or GitRunOptions()
    effective_timeout = (
        effective_options.timeout
        if effective_options.timeout is not None
        else GIT_SUBPROCESS_TIMEOUT_SECONDS
    )
    input_data = (
        effective_options.input_data
        if isinstance(effective_options.input_data, bytes)
        else effective_options.input_data.encode()
        if isinstance(effective_options.input_data, str)
        else None
    )
    cmd = ("git", *args)
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=_spawn_env(effective_options.env),
        input=input_data,
        capture_output=effective_options.capture_output,
        timeout=effective_timeout,
        check=False,
    )
    stdout_raw = completed.stdout if effective_options.capture_output else b""
    stderr_raw = completed.stderr if effective_options.capture_output else b""
    stdout = (
        stdout_raw.decode() if isinstance(stdout_raw, (bytes, bytearray)) else (stdout_raw or "")
    )
    stderr = (
        stderr_raw.decode() if isinstance(stderr_raw, (bytes, bytearray)) else (stderr_raw or "")
    )
    if effective_options.check and completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, list(cmd), stdout, stderr)
    return GitRunResult(args=cmd, returncode=completed.returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _patch_git_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.git.subprocess_runner._spawn_git", _fast_spawn_git)


@functools.lru_cache(maxsize=1)
def _default_policy() -> PolicyBundle:
    return load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")


@pytest.mark.parametrize("completion", ["prepared", "unrelated"])
def test_completed_retained_rebase_lands_or_replays_stale_intent(
    tmp_path: Path, completion: str
) -> None:
    from ralph.pipeline._pending_rebase_continue import prepare_pending_rebase

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
    from ralph.pipeline._pending_merge_commit import mark_pending_merge

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

    from ralph.pipeline._pending_merge_repair import repair_pending_merge
    from ralph.pipeline._pending_rebase_continue import prepare_pending_rebase
    from ralph.pipeline.auto_integrate_record import clear_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
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
    import ralph.pipeline._owned_landing_reintegration as owned
    from ralph.git.merge import UnmergedPathsObservation
    from ralph.pipeline._integration_action import RebaseOutcomeKind
    from ralph.pipeline.integration_observation import InProgressKind, Reading

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

    def fake_snapshot(*_args: object, **_kwargs: object) -> tuple[str, str]:
        sequence.append("backup_ref")
        return ("refs/ralph-backup/feature-abcdef12", "c" * 40)

    def fake_write(_root: Path, rec: IntegrationRecord) -> None:
        sequence.append("record")
        written.append(rec)

    def fake_run_git(args: object, **_kwargs: object) -> GitRunResult:
        argv = tuple(args) if isinstance(args, (list, tuple)) else (str(args),)
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
    monkeypatch.setattr(owned, "get_head_sha", lambda _root: "a" * 40)
    monkeypatch.setattr(owned, "current_branch_or_detached_marker", lambda _root: "feature")
    monkeypatch.setattr(
        owned,
        "create_stale_resolution_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(owned, "_move_feature_to_snapshot", lambda *_args: True)
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
