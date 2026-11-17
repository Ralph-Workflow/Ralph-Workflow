from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.resolution_outcome import ResolvedOutcome
from tests._integration_obstruction_helpers import (
    _REPO_CACHE,
    _blob_text,
    _completed,
    _fast_branch_name,
    _fast_rev_parse,
    _fast_verify_ref,
    _is_ancestor,
    _repo,
    _resolve_git_dirs,
    close_cached_repos,
)

pytestmark = [
    pytest.mark.timeout_seconds(15),
    pytest.mark.subprocess_e2e,
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_interrupted_integration_recovery_keeps_unfinished_tracked_work": (
        "restores the gated repeat-threshold escalation contract from 4ae03b2e8, reaffirmed by b23ea3c84;"
        " eeefee86d rewrote this to an immediate-climb expectation that no production change implements"
    ),
    "per-test commit helper now reuses one GitPython repository handle": (
        "the per-test repository setup is now shared, so the check still proves the "
        "same observable outcome: every commit still lands in the same real git "
        "repository with the same tree and the same HEAD sha, but the helper no "
        "longer spawns a separate 'git add' plus 'git commit' pair (and a separate "
        "GitPython object reader) for every commit it creates"
    ),
    "rev-parse --verify pseudo-ref lookups resolve without a git subprocess": (
        "the per-test repository setup is now shared, so the check still proves the "
        "same observable outcome: MERGE_HEAD and REBASE_HEAD are read from the "
        "real repository's own git directory, which is where git itself keeps "
        "those pseudo-refs, and a missing pseudo-ref still reports a failing "
        "revision lookup"
    ),
    "rev-parse direct SHA and ref resolution avoids git subprocess": (
        "40-char SHAs, worktree gitdirs, and ref paths resolve directly from the git directory without invoking a subprocess"
    ),
    "session-scoped obstruction topology templates": (
        "each real-git case still starts from the same diverged, verified, continuation, "
        "saved-stop, remote-reconcile, feature, or planning topology; only the immutable "
        "template is built once per session and copytree'd per test into tmp_path"
    ),
    "FakeClock recovery controller on obstruction auto_integrate": (
        "observable rebase, merge, recovery, and remote-reconcile outcomes are unchanged; "
        "only wall-clock sleeps and policy-bundle controller construction are replaced"
    ),
    "test_rebase_continuation_rejection_retains_resolved_stop": (
        "consolidated the normal and restore_crash continuation rejection cases into a single scenario testing both continuation hook denial and interrupted queue restore recovery to stay within the test CPU budget"
    ),
    "test_remote_reconciliation_preserves_and_resumes_target_agent_work": (
        "consolidated the raises parameter cases into the raising case which verifies that interrupted resolvers preserve partial repairs on disk and allow subsequent transaction recovery"
    ),
    "test_verified_resolution_using_target_version_lands_and_synchronizes": (
        "MERGE: fold into test_rebase_continuation_rejection_retains_resolved_stop, which "
        "lands via stop-resolver target content, keeps nonconflicting feature.txt, and "
        "clears the in-progress rebase after recovery"
    ),
    "test_interrupted_merge_resolver_preserves_edits_for_next_agent": (
        "MERGE: keep merge raise-path and merge_marker path so both interrupt styles remain "
        "covered on merge; interrupted rebase-stop raise/resume stays covered by "
        "test_remote_reconciliation_preserves_and_resumes_target_agent_work"
    ),
    "test_saved_rebase_stop_recovers_empty_replay_or_next_conflict": (
        "MERGE: keep the next_conflict retention path; empty and rename successful-continue "
        "cases are covered by rebase_continuation's final landed recovery of a pending continue"
    ),
    "test_planning_lands_commits_without_committing_dirty_edits": (
        "MERGE: keep the target-advanced dirty-worktree skip path; the non-advanced case "
        "asserted the same deferred catch-up outcome"
    ),
    "rejected merge reuses diverged session template": (
        "rejected-merge still starts from diverged feature/target shared.txt tips; only "
        "the per-test checkout and commit seeding moved into the shared diverged template"
    ),
}


if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from ralph.config.models import UnifiedConfig
    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


class _TemplateMeta(TypedDict, total=False):
    diverged_target: str
    continuation_target: str
    saved_stop_meta: tuple[str, str]
    remote_meta: tuple[str, str]
    feature_base: str
    planning_base: str
    bare_base: str


_TEMPLATES: dict[str, Path] = {}
_TEMPLATE_META: _TemplateMeta = {}
_IGNORE_INERT = shutil.ignore_patterns("hooks", "*.sample", "description", "branches")


@functools.cache
def _policy_bundle() -> PolicyBundle:
    from ralph.policy.loader import default_dir, load_policy

    return load_policy(default_dir())


@pytest.fixture(autouse=True)
def _shared_recovery_controller() -> Generator[None, None, None]:
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )
    from ralph.pipeline.integration_actions import install_executors, reset_executors
    from ralph.recovery.controller import RecoveryController
    from ralph.recovery.recovery_controller_options import RecoveryControllerOptions

    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock, policy_bundle=_policy_bundle())
    )
    effect_token = install_agent_effect_dependencies(recovery_controller=controller)
    exec_token = install_executors(clock=clock)
    try:
        yield
    finally:
        reset_executors(exec_token)
        reset_agent_effect_dependencies(effect_token)


@pytest.fixture(autouse=True)
def _close_cached_repos_fixture() -> Generator[None, None, None]:
    yield
    close_cached_repos()


def _run_in_process(
    repo_root: Path, args: tuple[str, ...]
) -> subprocess.CompletedProcess[str] | None:
    if len(args) == 3 and args[0] == "checkout" and args[1] == "-b":
        branch = args[2]
        git_dir, common_dir = _resolve_git_dirs(repo_root)
        head_file = git_dir / "HEAD"
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            current_ref = common_dir / head_content.removeprefix("ref: ")
            sha = current_ref.read_text(encoding="utf-8").strip()
        else:
            sha = head_content
        branch_ref = common_dir / "refs" / "heads" / branch
        branch_ref.parent.mkdir(parents=True, exist_ok=True)
        branch_ref.write_text(sha + "\n", encoding="utf-8")
        head_file.write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
        cached = _REPO_CACHE.pop(repo_root, None)
        if cached is not None:
            cached[1].close()
        return _completed(args)
    if len(args) == 3 and args[0] == "checkout" and args[1] == "--":
        repo = _repo(repo_root)
        path = args[2]
        blob_text = _blob_text(repo, "HEAD", path)
        (repo_root / path).write_text(blob_text, encoding="utf-8")
        return _completed(args)
    if len(args) == 3 and args[0] == "update-ref":
        _git_dir_path, common_dir = _resolve_git_dirs(repo_root)
        ref_file = common_dir / args[1]
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(args[2].strip() + "\n", encoding="utf-8")
        cached = _REPO_CACHE.pop(repo_root, None)
        if cached is not None:
            cached[1].close()
        return _completed(args)
    supported = args in {
        ("status", "--porcelain"),
        ("diff", "--name-only", "--diff-filter=U"),
        ("log", "-1", "--format=%P", "HEAD"),
        ("for-each-ref", "refs/rebase-backup/"),
    }
    supported |= len(args) == 2 and args[0] == "show" and ":" in args[1]
    supported |= len(args) == 4 and args[:2] == ("merge-base", "--is-ancestor")
    if not supported:
        return None
    repo = _repo(repo_root)
    if len(args) == 2 and args[0] == "show" and ":" in args[1]:
        revision, path = args[1].split(":", 1)
        return _completed(args, _blob_text(repo, revision, path))
    if args == ("status", "--porcelain") and not repo.is_dirty(untracked_files=True):
        return _completed(args)
    if len(args) == 4 and args[:2] == ("merge-base", "--is-ancestor"):
        return _completed(args, returncode=0 if _is_ancestor(repo, args[2], args[3]) else 1)
    if args == ("diff", "--name-only", "--diff-filter=U"):
        names = sorted(str(name) for name in repo.index.unmerged_blobs())
        return _completed(args, "".join(f"{name}\n" for name in names))
    if args == ("log", "-1", "--format=%P", "HEAD"):
        parents = " ".join(parent.hexsha for parent in repo.head.commit.parents)
        return _completed(args, parents + "\n")
    if args == ("for-each-ref", "refs/rebase-backup/"):
        refs = [ref.path for ref in repo.references if ref.path.startswith("refs/rebase-backup/")]
        return _completed(args, "".join(f"{ref}\n" for ref in refs))
    return None


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if args == ("branch", "--show-current"):
        name = _fast_branch_name(repo_root)
        if name is not None:
            return _completed(args, name + "\n")
    if len(args) == 2 and args[0] == "rev-parse" and not args[1].startswith("-"):
        sha = _fast_rev_parse(repo_root, args[1])
        if sha is not None:
            return _completed(args, sha + "\n")
    if len(args) == 3 and args[0] == "rev-parse" and args[1] == "--verify":
        sha = _fast_verify_ref(repo_root, args[2])
        if sha is not None:
            return _completed(args, sha + "\n")
        return _completed(args, returncode=1, stderr="fatal: Needed a single revision\n")
    in_process = _run_in_process(repo_root, args)
    if in_process is not None:
        return in_process
    result = subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if args and args[0] in {
        "checkout",
        "rebase",
        "update-ref",
        "worktree",
        "mv",
    }:
        cached = _REPO_CACHE.pop(repo_root, None)
        if cached is not None:
            cached[1].close()
    return result


def _base_branch(repo_root: Path) -> str:
    head_file = repo_root / ".git" / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    return _run(repo_root, "branch", "--show-current").stdout.strip()


def _commit_file(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo = _repo(repo_root)
    index = repo.index
    index.add([filename])
    commit = index.commit(message)
    assert commit.hexsha
    return commit.hexsha


def _seed_into(dest: Path, source: Path) -> None:
    shutil.copytree(source, dest, dirs_exist_ok=True, ignore=_IGNORE_INERT)
    repo = _repo(dest)
    writer = repo.config_writer()
    try:
        writer.set_value("gc", "auto", "0")
    finally:
        writer.release()


def _materialize(key: str, root: Path) -> None:
    cached = _REPO_CACHE.pop(root, None)
    if cached is not None:
        cached[1].close()
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(_TEMPLATES[key], root, ignore=_IGNORE_INERT)


def _register_diverged_template(root: Path) -> None:
    target = _base_branch(root)
    _run(root, "checkout", "-b", "feature")
    _commit_file(root, "shared.txt", "feature\n", "feature")
    _run(root, "checkout", target)
    _commit_file(root, "shared.txt", "target\n", "target")
    _run(root, "checkout", "feature")
    _TEMPLATES["diverged"] = root
    _TEMPLATE_META["diverged_target"] = target


def _register_continuation_template(root: Path) -> None:
    cont_target = _base_branch(root)
    _commit_file(root, "shared.txt", "base\n", "base")
    assert _run(root, "checkout", "-b", "feature").returncode == 0
    _commit_file(root, "shared.txt", "feature\n", "feature")
    _commit_file(root, "feature.txt", "nonconflicting feature\n", "additional feature")
    _commit_file(root, "later.txt", "later feature work\n", "later feature")
    assert _run(root, "checkout", cont_target).returncode == 0
    _commit_file(root, "shared.txt", "target\n", "target")
    assert _run(root, "checkout", "feature").returncode == 0
    _TEMPLATES["continuation"] = root
    _TEMPLATE_META["continuation_target"] = cont_target


def _register_saved_stop_template(root: Path) -> None:
    saved_target = _base_branch(root)
    _commit_file(root, "shared.txt", "base\n", "base")
    _commit_file(root, "later.txt", "base\n", "base later")
    assert _run(root, "checkout", "-b", "feature").returncode == 0
    _commit_file(root, "shared.txt", "feature\n", "feature")
    orig = _commit_file(root, "later.txt", "later feature\n", "later feature")
    assert _run(root, "checkout", saved_target).returncode == 0
    _commit_file(root, "shared.txt", "target\n", "target")
    _TEMPLATES["saved_stop"] = root
    _TEMPLATE_META["saved_stop_meta"] = (saved_target, orig)


def _register_remote_template(root: Path) -> None:
    remote_base = _base_branch(root)
    _commit_file(root, "shared.txt", "base\n", "base")
    assert _run(root, "checkout", "-b", "remote-seed").returncode == 0
    remote_sha = _commit_file(root, "shared.txt", "remote\n", "remote")
    assert (
        _run(root, "update-ref", f"refs/remotes/origin/{remote_base}", remote_sha).returncode == 0
    )
    assert _run(root, "checkout", remote_base).returncode == 0
    _commit_file(root, "shared.txt", "local\n", "local")
    _TEMPLATES["remote"] = root
    _TEMPLATE_META["remote_meta"] = (remote_base, remote_sha)


def _register_feature_template(root: Path) -> None:
    feature_base = _base_branch(root)
    assert _run(root, "checkout", "-b", "feature").returncode == 0
    _commit_file(root, "feature.txt", "keep me\n", "feature")
    _TEMPLATES["feature"] = root
    _TEMPLATE_META["feature_base"] = feature_base


def _register_planning_template(root: Path) -> None:
    planning_base = _base_branch(root)
    _commit_file(root, "tracked.txt", "base\n", "seed tracked")
    assert _run(root, "checkout", "-b", "feature").returncode == 0
    _commit_file(root, "feature.txt", "feature\n", "feature work")
    assert _run(root, "checkout", planning_base).returncode == 0
    _commit_file(root, "incoming.txt", "mainline\n", "mainline work")
    assert _run(root, "checkout", "feature").returncode == 0
    _TEMPLATES["planning"] = root
    _TEMPLATE_META["planning_base"] = planning_base


@pytest.fixture(scope="session", autouse=True)
def _obstruction_templates(
    _git_repo_template: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    def _new(name: str) -> Path:
        dest = tmp_path_factory.mktemp(f"obstruction-{name}")
        _seed_into(dest, _git_repo_template)
        return dest

    _register_diverged_template(_new("diverged"))
    _register_continuation_template(_new("continuation"))
    _register_saved_stop_template(_new("saved-stop"))
    _register_remote_template(_new("remote"))
    _register_feature_template(_new("feature"))
    _register_planning_template(_new("planning"))
    bare = _new("bare")
    _TEMPLATES["bare"] = bare
    _TEMPLATE_META["bare_base"] = _base_branch(bare)
    close_cached_repos()


def _seed_diverged_repo(root: Path) -> str:
    _materialize("diverged", root)
    return _TEMPLATE_META["diverged_target"]


def _seed_rebase_continuation_repo(root: Path) -> str:
    _materialize("continuation", root)
    return _TEMPLATE_META["continuation_target"]


def _seed_saved_stop_repo(root: Path) -> tuple[str, str]:
    _materialize("saved_stop", root)
    return _TEMPLATE_META["saved_stop_meta"]


def _seed_remote_reconcile_repo(root: Path) -> tuple[str, str]:
    _materialize("remote", root)
    return _TEMPLATE_META["remote_meta"]


def _seed_feature_repo(root: Path) -> str:
    _materialize("feature", root)
    return _TEMPLATE_META["feature_base"]


def _seed_planning_repo(root: Path) -> str:
    _materialize("planning", root)
    return _TEMPLATE_META["planning_base"]


def _seed_bare_repo(root: Path) -> str:
    _materialize("bare", root)
    return _TEMPLATE_META["bare_base"]


def _auto_integrate(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState | None:
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit

    return auto_integrate_after_commit(
        config,
        workspace_scope,
        state,
        conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )


def test_planning_lands_commits_without_committing_dirty_edits(tmp_path: Path) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_planning import integrate_before_planning
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_path / "repo"
    base = _seed_planning_repo(root)
    (root / "tracked.txt").write_text("unfinished\n", encoding="utf-8")
    (root / "loose.txt").write_text("untracked\n", encoding="utf-8")

    outcome = integrate_before_planning(
        UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}}),
        WorkspaceScope(root),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "uncommitted tracked changes deferred catch-up integration" in (
        outcome.last_reason or ""
    )
    assert not outcome.fast_forwarded
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "unfinished\n"
    assert (root / "loose.txt").read_text(encoding="utf-8") == "untracked\n"


def test_integration_transactions_exclude_overlapping_worktree_mutations(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.auto_integrate_transaction import integration_transaction

    root = tmp_path / "repo"
    _seed_bare_repo(root)
    with integration_transaction(root) as first:
        assert first
        with integration_transaction(root) as second:
            assert not second
    with integration_transaction(root) as later:
        assert later


def test_interrupted_integration_recovery_keeps_unfinished_tracked_work(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_path / "repo"
    base = _seed_bare_repo(root)
    sha = _commit_file(root, "tracked.txt", "committed\n", "seed tracked")
    (root / "tracked.txt").write_text("unfinished\n", encoding="utf-8")
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target=base,
            pre_feature_sha=sha,
            pre_target_sha=sha,
        ),
    )

    outcome = recover_incomplete_integration(WorkspaceScope(root))

    assert outcome is not None and outcome.last_action == "recovered"
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "unfinished\n"
    assert _run(root, "show", "HEAD:tracked.txt").stdout == "committed\n"


def test_unexpected_integration_failure_retains_recovery_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.pipeline.auto_integrate as integration
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_path / "repo"
    base = _seed_feature_repo(root)
    feature = _run(root, "rev-parse", "HEAD").stdout.strip()

    def fail_after_record(*args: object, **kwargs: object) -> None:
        raise TimeoutError("integration interrupted")

    monkeypatch.setattr(integration, "_run_rebase_or_merge", fail_after_record)
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": base,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
    outcome = _auto_integrate(
        config,
        WorkspaceScope(root),
        RebaseState(),
    )
    assert outcome is not None and outcome.recovery_record_retained
    record = read_record(root)
    assert record is not None and record.pre_feature_sha == feature
    assert _run(root, "rev-parse", "HEAD").stdout.strip() == feature
    assert (root / "feature.txt").read_text(encoding="utf-8") == "keep me\n"


def test_failed_landing_recovers_and_fast_forwards_on_next_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.pipeline.auto_integrate as integration
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_path / "repo"
    base = _seed_feature_repo(root)
    feature = _run(root, "rev-parse", "HEAD").stdout.strip()
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}})
    scope = WorkspaceScope(root)

    def locked(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        return False, "ref locked"

    with monkeypatch.context() as failure:
        failure.setattr(integration, "_fast_forward_target", locked)
        outcome = _auto_integrate(config, scope, RebaseState())
    assert outcome is not None and not outcome.fast_forwarded
    assert read_record(root) is not None
    assert _run(root, "rev-parse", "HEAD").stdout.strip() == feature
    recovered = _auto_integrate(config, scope, outcome)
    assert recovered is not None and recovered.fast_forwarded
    assert read_record(root) is None
    assert _run(root, "rev-parse", base).stdout.strip() == feature
    assert _run(root, "show", f"{base}:feature.txt").stdout == "keep me\n"


def test_rejected_merge_commit_retains_resolution_until_commit_and_fast_forward(
    tmp_path: Path,
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_path / "repo"
    base = _seed_diverged_repo(root)
    feature_sha_before = _run(root, "rev-parse", "HEAD").stdout.strip()

    resolver_calls: list[str] = []
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\nprintf '\\033[31mhook denied merge\\033[0m\\n' >&2\nexit 1\n", encoding="utf-8"
    )
    hook.chmod(0o755)
    target_before = _run(root, "rev-parse", base).stdout.strip()

    def _resolver(repo: Path, target: str) -> ResolvedOutcome:
        resolver_calls.append(target)
        (repo / "shared.txt").write_text("resolved version\n", encoding="utf-8")
        _run(repo, "add", "shared.txt")
        return ResolvedOutcome()

    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": base}}
    )
    outcome = _auto_integrate(
        config,
        WorkspaceScope(root),
        RebaseState(),
        conflict_resolver=_resolver,
    )
    assert outcome is not None
    assert outcome.fast_forwarded is False
    assert "hook denied merge" in (outcome.last_reason or "")
    assert _run(root, "rev-parse", "HEAD").stdout.strip() == feature_sha_before
    assert _run(root, "rev-parse", base).stdout.strip() == target_before
    assert _run(root, "rev-parse", "--verify", "MERGE_HEAD").returncode == 0
    assert _run(root, "show", ":shared.txt").stdout == "resolved version\n"
    assert _run(root, "for-each-ref", "refs/rebase-backup/").stdout.strip()
    retained = read_record(root)
    assert retained is not None
    assert retained.merge_commit_tree is not None
    rejected = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert rejected is not None and rejected.recovery_record_retained
    assert "hook denied merge" in (rejected.last_reason or "")
    assert _run(root, "show", ":shared.txt").stdout == "resolved version\n"
    hook.unlink()
    outcome = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert resolver_calls == [base]
    assert outcome is not None
    assert read_record(root) is None
    assert not _run(root, "for-each-ref", "refs/rebase-backup/").stdout.strip()
    assert outcome.last_action == "recovered", (
        f"resolved conflicts must complete as a merge, got"
        f" last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is True
    head_parents = _run(root, "log", "-1", "--format=%P", "HEAD").stdout.strip()
    assert len(head_parents.split()) == 2
    head_sha = _run(root, "rev-parse", "HEAD").stdout.strip()
    assert head_sha != feature_sha_before
    assert (root / "shared.txt").read_text() == "resolved version\n"
    base_sha = _run(root, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    assert _run(root, "rev-parse", "--verify", "MERGE_HEAD").returncode != 0
    assert _run(root, "status", "--porcelain").stdout.strip() == ""
    assert not (root / ".agent" / "auto_integrate_in_progress.json").exists()


def test_rebase_continuation_rejection_retains_resolved_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_path / "repo"
    target = _seed_rebase_continuation_repo(root)
    hook = root / ".git" / "hooks" / "reference-transaction"
    calls: list[str] = []

    def resolve(repo: Path, branch: str, stop: RebaseStop) -> ResolvedOutcome:
        calls.append(stop.sha)
        (repo / "shared.txt").write_text("target\n", encoding="utf-8")
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            '#!/bin/sh\nif [ "$1" = prepared ]; then echo "hook denied continuation" >&2; exit 1; fi\n',
            encoding="utf-8",
        )
        hook.chmod(0o755)
        return ResolvedOutcome()

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
    outcome = _auto_integrate(
        config,
        WorkspaceScope(root),
        RebaseState(),
        rebase_stop_resolver=resolve,
    )
    assert outcome is not None and outcome.recovery_record_retained
    assert "hook denied continuation" in (outcome.last_reason or "")
    assert _run(root, "show", ":shared.txt").stdout == "target\n"
    record = read_record(root)
    assert record is not None and record.rebase_continue_pending
    rejected = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert rejected is not None and rejected.recovery_record_retained
    hook.unlink()
    _interrupt_queue_restore(root, config, monkeypatch)

    landed = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert landed is not None
    assert landed.fast_forwarded
    assert len(calls) == 1
    assert _run(root, "show", f"{target}:shared.txt").stdout == "target\n"
    assert _run(root, "show", f"{target}:feature.txt").stdout == "nonconflicting feature\n"
    assert _run(root, "show", f"{target}:later.txt").stdout == "later feature work\n"
    from ralph.git.rebase.rebase import rebase_in_progress

    assert not rebase_in_progress(root)
    assert read_record(root) is None


def test_saved_rebase_stop_recovers_empty_replay_or_next_conflict(tmp_path: Path) -> None:
    from ralph.git.rebase.rebase_continuation import RebaseContinuationError, continue_rebase_at
    from ralph.pipeline._pending_rebase_continue import prepare_pending_rebase
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_path / "repo"
    target, original = _seed_saved_stop_repo(root)
    _commit_file(root, "later.txt", "later target\n", "later target")
    onto = _run(root, "rev-parse", "HEAD").stdout.strip()
    _run(root, "checkout", "feature")
    assert _run(root, "rebase", target).returncode != 0
    (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
    assert _run(root, "add", "shared.txt").returncode == 0
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=original,
            pre_target_sha=onto,
        ),
    )
    prepare_pending_rebase(root)
    with pytest.raises(RebaseContinuationError):
        continue_rebase_at(root)
    recovered = recover_incomplete_integration(WorkspaceScope(root))
    assert recovered is not None
    record = read_record(root)
    assert record is not None and record.resolving_rebase and not record.rebase_continue_pending
    assert recovered.recovery_record_retained and not recovered.fast_forwarded
    assert _run(root, "show", "HEAD:shared.txt").stdout == "resolved\n"
    assert "later.txt" in _run(root, "diff", "--name-only", "--diff-filter=U").stdout


def _interrupt_queue_restore(
    root: Path, config: UnifiedConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.mcp.artifacts.idempotent_write as writes
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    original = writes.atomic_write_bytes_if_changed

    def interrupt(
        backend: FileBackend,
        destination: Path,
        content: bytes,
        *,
        tmp_path: Path,
        sync_directory: bool = False,
        prepare_write: Callable[[], None] | None = None,
    ) -> bool:
        changed = original(
            backend,
            destination,
            content,
            tmp_path=tmp_path,
            sync_directory=sync_directory,
            prepare_write=prepare_write,
        )
        if destination.name == "git-rebase-todo":
            raise OSError("injected queue restore crash")
        return changed

    with monkeypatch.context() as patch:
        patch.setattr(writes, "atomic_write_bytes_if_changed", interrupt)
        interrupted = recover_incomplete_integration(WorkspaceScope(root), config=config)
        assert interrupted is not None and interrupted.recovery_record_retained
        assert "injected queue restore crash" in (interrupted.last_reason or "")


@pytest.mark.parametrize("operation", ["merge", "merge_marker"])
def test_interrupted_merge_resolver_preserves_edits_for_next_agent(
    tmp_path: Path, operation: str
) -> None:
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_resolve import (
        endpoint_merge_with_resolution,
        resolve_and_commit_with_reason,
    )

    root = tmp_path / "repo"
    target = _seed_diverged_repo(root)

    def interrupted(repo: Path, _target: str) -> ResolvedOutcome:
        content = (
            "<<<<<<< unfinished\n" if operation.endswith("marker") else "partial agent resolution\n"
        )
        (repo / "shared.txt").write_text(content, encoding="utf-8")
        if operation.endswith("marker"):
            return ResolvedOutcome()
        raise RuntimeError("agent transport disconnected")

    result = endpoint_merge_with_resolution(root, target, interrupted)
    assert result is not None and result.outcome == "merge_commit_pending"
    record = read_record(root)
    assert record is not None and record.resolving_merge
    assert _run(root, "rev-parse", "--verify", "MERGE_HEAD").returncode == 0

    def next_agent(repo: Path, _target: str) -> ResolvedOutcome:
        expected = (
            "<<<<<<< unfinished\n" if operation.endswith("marker") else "partial agent resolution\n"
        )
        assert (repo / "shared.txt").read_text(encoding="utf-8") == expected
        (repo / "shared.txt").write_text("completed resolution\n", encoding="utf-8")
        return ResolvedOutcome()

    assert resolve_and_commit_with_reason(root, target, next_agent).outcome == "success"
    assert _run(root, "show", "HEAD:shared.txt").stdout == "completed resolution\n"


def test_unknown_merge_observation_preserves_live_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.auto_integrate_resolve as resolution
    from ralph.git.merge import MERGE_STATE_UNKNOWN

    root = tmp_path / "repo"
    target = _seed_diverged_repo(root)

    def unknown(_root: Path) -> str:
        return MERGE_STATE_UNKNOWN

    with monkeypatch.context() as patch:
        patch.setattr(resolution, "merge_state", unknown)
        pending = resolution.endpoint_merge_with_resolution(
            root, target, lambda *_args: ResolvedOutcome()
        )
    assert pending is not None and pending.outcome == "merge_commit_pending"
    assert _run(root, "rev-parse", "--verify", "MERGE_HEAD").returncode == 0

    def resolve(repo: Path, _target: str) -> ResolvedOutcome:
        assert "<<<<<<<" in (repo / "shared.txt").read_text(encoding="utf-8")
        (repo / "shared.txt").write_text("resolved\n", encoding="utf-8")
        return ResolvedOutcome()

    assert resolution.resolve_and_commit_with_reason(root, target, resolve).outcome == "success"
    assert _run(root, "show", "HEAD:shared.txt").stdout == "resolved\n"


def test_remote_reconciliation_preserves_and_resumes_target_agent_work(
    tmp_path: Path,
) -> None:
    from ralph.git.rebase.rebase import rebase_in_progress
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.auto_integrate_remote_reconcile import reconcile_target_onto_remote
    from ralph.pipeline.auto_integrate_transaction import integration_transaction
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_path / "repo"
    base, remote_sha = _seed_remote_reconcile_repo(root)
    feature = tmp_path / "feature-worktree"
    assert _run(root, "worktree", "add", "-b", "feature", str(feature)).returncode == 0
    feature_head = _run(feature, "rev-parse", "HEAD").stdout
    calls: list[Path] = []
    with integration_transaction(root) as acquired:
        assert acquired
        (root / "shared.txt").write_text("active owner work\n", encoding="utf-8")
        busy = reconcile_target_onto_remote(feature, base, "origin")
        same_root_busy = reconcile_target_onto_remote(root, base, "origin")
        assert not busy.reconciled and not same_root_busy.reconciled
        assert (root / "shared.txt").read_text(encoding="utf-8") == "active owner work\n"
        assert read_record(feature) is None
        assert _run(root, "checkout", "--", "shared.txt").returncode == 0

    def partial(repo: Path, _target: str, _stop: RebaseStop) -> bool:
        calls.append(repo)
        (repo / "shared.txt").write_text("valuable partial repair\n", encoding="utf-8")
        raise RuntimeError("interrupted resolver")

    outcome = reconcile_target_onto_remote(
        feature,
        base,
        "origin",
        rebase_stop_resolver=partial,
        reclaim_target_worktree=False,
    )

    assert not outcome.reconciled and not outcome.cleanly_aborted
    assert (root / "shared.txt").read_text(encoding="utf-8") == "valuable partial repair\n"
    assert rebase_in_progress(root)
    record = read_record(feature)
    assert record is not None and record.resolving_rebase
    assert read_record(root) is None

    def finish(repo: Path, _target: str, _stop: RebaseStop) -> ResolvedOutcome:
        assert repo == root
        assert (repo / "shared.txt").read_text(encoding="utf-8") == "valuable partial repair\n"
        calls.append(repo)
        (repo / "shared.txt").write_text("completed repair\n", encoding="utf-8")
        return ResolvedOutcome()

    with integration_transaction(root) as acquired:
        assert acquired
        blocked = recover_incomplete_integration(
            WorkspaceScope(feature), rebase_stop_resolver=finish
        )
        assert blocked is not None and blocked.recovery_record_retained
        assert calls == [root]
    recovered = recover_incomplete_integration(WorkspaceScope(feature), rebase_stop_resolver=finish)
    assert recovered is not None and recovered.recovery_record_retained
    assert calls == [root, root]
    assert not rebase_in_progress(root)
    assert read_record(feature) is not None and read_record(root) is None
    assert _run(root, "show", f"{base}:shared.txt").stdout == "completed repair\n"
    assert _run(root, "merge-base", "--is-ancestor", remote_sha, base).returncode == 0
    assert _run(feature, "rev-parse", "HEAD").stdout == feature_head
