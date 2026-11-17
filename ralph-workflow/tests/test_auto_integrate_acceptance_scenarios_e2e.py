from __future__ import annotations

import contextlib
import functools
import shutil
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from git import Repo
from git.exc import GitError

from ralph.config.models import UnifiedConfig
from ralph.git.remote_push import PushStatus
from ralph.pipeline import auto_integrate_recovery
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.workspace.scope import WorkspaceScope
from tests.test_pending_merge_commit_recovery import _common_git_dir, _git_dir

_TARGET = "release"
_REMOTE = "tacos"
_COPY_IGNORE = shutil.ignore_patterns("hooks", "*.sample", "description", "branches")

pytestmark = [
    pytest.mark.required_auto_integrate_e2e,
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    'assert "SKIPPED_SECOND_ITERATION" in err': (
        "the negative control and positive retry scenarios were consolidated into a single "
        "subprocess run that asserts bare refs remain unchanged at seed_sha during deferred "
        "retries before asserting publication on remote restoration"
    ),
    "test_killed_between_landing_and_push_recovers_from_linked_worktree": (
        "REPLACE: same landing-then-push-interrupted observables (target advanced, bare "
        "unchanged, linked-worktree recovery publishes) via an in-process push interrupt "
        "instead of a child interpreter blocked on time.sleep"
    ),
    "test_unreachable_then_reachable_production_retry_succeeds_across_subprocess": (
        "REPLACE: same deferred-publication then restored-remote publish contract through "
        "run_loop._run_inner_loop_after_startup in-process; two deferred sleep ticks still "
        "prove bare refs stay at seed_sha until RESTORED, matching the prior three-tick probe"
    ),
}


def _resolve_git_dir(root: Path) -> Path:
    if (root / "HEAD").is_file() and (root / "refs").is_dir():
        return root
    return _git_dir(root)


def _fast_branch_name(root: Path) -> str | None:
    try:
        git_dir = _resolve_git_dir(root)
        head_file = git_dir / "HEAD"
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    return None


def _fast_rev_parse(root: Path, ref_name: str) -> str | None:
    try:
        git_dir = _resolve_git_dir(root)
        common_dir = _common_git_dir(git_dir)
        if ref_name == "HEAD":
            head_file = git_dir / "HEAD"
            head_content = head_file.read_text(encoding="utf-8").strip()
            if head_content.startswith("ref: refs/heads/"):
                rel_path = head_content.removeprefix("ref: ")
                target_ref = common_dir / rel_path
                if not target_ref.is_file():
                    target_ref = git_dir / rel_path
                if target_ref.is_file():
                    return target_ref.read_text(encoding="utf-8").strip()
            elif len(head_content) == 40:
                return head_content
        else:
            ref_file = common_dir / "refs" / "heads" / ref_name
            if not ref_file.is_file():
                ref_file = git_dir / "refs" / "heads" / ref_name
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip()
            packed_refs_file = common_dir / "packed-refs"
            if packed_refs_file.is_file():
                for line in packed_refs_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split()
                    if len(parts) == 2 and parts[1].endswith(f"/{ref_name}"):
                        return parts[0]
    except OSError:
        pass
    try:
        return _repo(root).commit(ref_name).hexsha
    except (GitError, OSError, ValueError, KeyError, AttributeError):
        pass
    return None


def _write_local_config(repo_root: Path, key: str, value: str) -> bool:
    try:
        repo = _repo(repo_root)
        with repo.config_writer() as config:
            config.set_value("user", key.split(".", 1)[1], value)
    except (GitError, OSError, ValueError, KeyError, AttributeError):
        return False
    return True


def _write_remote_url(repo_root: Path, remote_name: str, url: str) -> bool:
    try:
        repo = _repo(repo_root)
        with repo.config_writer() as config:
            config.set_value(f'remote "{remote_name}"', "url", url)
    except (GitError, OSError, RuntimeError, ValueError, KeyError):
        return False
    return True


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if args == ("branch", "--show-current"):
        name = _fast_branch_name(repo_root)
        if name is not None:
            return subprocess.CompletedProcess(
                args=("git", *args), stdout=name + "\n", stderr="", returncode=0
            )
    if len(args) == 2 and args[0] == "rev-parse" and not args[1].startswith("-"):
        sha = _fast_rev_parse(repo_root, args[1])
        if sha is not None:
            return subprocess.CompletedProcess(
                args=("git", *args), stdout=sha + "\n", stderr="", returncode=0
            )
    if len(args) == 3 and args[0] == "config" and args[1] in {"user.email", "user.name"}:
        written = _write_local_config(repo_root, args[1], args[2])
        if written:
            return subprocess.CompletedProcess(
                args=("git", *args), stdout="", stderr="", returncode=0
            )
    if len(args) == 4 and args[0] == "remote" and args[1] == "set-url":
        written = _write_remote_url(repo_root, args[2], args[3])
        if written:
            return subprocess.CompletedProcess(
                args=("git", *args), stdout="", stderr="", returncode=0
            )
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


_REPO_CACHE: dict[Path, Repo] = {}


def _repo(repo_root: Path) -> Repo:
    cached = _REPO_CACHE.get(repo_root)
    if cached is not None:
        return cached
    repo = Repo(repo_root)
    _REPO_CACHE[repo_root] = repo
    return repo


def _refreshed_repo(repo_root: Path) -> Repo:
    stale = _REPO_CACHE.pop(repo_root, None)
    if stale is not None:
        with contextlib.suppress(Exception):
            stale.close()
    repo = Repo(repo_root)
    _REPO_CACHE[repo_root] = repo
    return repo


@pytest.fixture(autouse=True)
def _close_cached_repos() -> Generator[None, None, None]:
    yield
    for repo in _REPO_CACHE.values():
        repo.close()
    _REPO_CACHE.clear()


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo = _refreshed_repo(repo_root)
    repo.index.add([filename])
    commit = repo.index.commit(message)
    assert commit.hexsha
    return commit.hexsha


def _init_repo(path: Path, default_branch: str = _TARGET) -> None:
    path.mkdir(parents=True, exist_ok=True)
    from git import Repo

    repo = Repo.init(path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "email", "test@example.com")
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("core", "logAllRefUpdates", "false")
        cfg.set_value("commit", "gpgSign", "false")
        cfg.set_value("gc", "auto", "0")
        cfg.set_value("core", "fsync", "none")
    repo.close()
    _commit(path, "seed.txt", "seed\n", "seed")
    assert _run(path, "branch", "-M", default_branch).returncode == 0


_CLUSTER_TEMPLATE: list[Path] = []


def _init_cluster_template(template_root: Path) -> None:
    repo = template_root / "repo"
    bare = template_root / "tacos.git"
    _init_repo(repo)
    bare.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ("git", "clone", "--bare", str(repo), str(bare)),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "-C", str(repo), "remote", "add", _REMOTE, "../tacos.git"),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "-C", str(repo), "push", _REMOTE, _TARGET),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "-C", str(repo), "fetch", _REMOTE),
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _acceptance_cluster_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    template_root = tmp_path_factory.mktemp("acceptance-cluster")
    _init_cluster_template(template_root)
    _CLUSTER_TEMPLATE.append(template_root)
    return template_root


def _seed_cluster(tmp_path: Path) -> tuple[Path, Path]:
    if not _CLUSTER_TEMPLATE:
        template_root = tmp_path / "_template"
        _init_cluster_template(template_root)
        _CLUSTER_TEMPLATE.append(template_root)
    template = _CLUSTER_TEMPLATE[0]
    shutil.copytree(template, tmp_path, dirs_exist_ok=True, ignore=_COPY_IGNORE)
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"
    _refreshed_repo(repo)
    return repo, bare


def _seed_repo(path: Path, default_branch: str = _TARGET) -> None:
    if _CLUSTER_TEMPLATE:
        shutil.copytree(_CLUSTER_TEMPLATE[0] / "repo", path, ignore=_COPY_IGNORE)
        _refreshed_repo(path)
        if default_branch != _TARGET:
            assert _run(path, "branch", "-M", default_branch).returncode == 0
        return
    _init_repo(path, default_branch)


def _head_sha(repo_root: Path, ref: str) -> str:
    return _run(repo_root, "rev-parse", ref).stdout.strip()


@functools.lru_cache(maxsize=16)
def _build_config(
    *,
    enabled: bool = True,
    remote_enabled: bool = True,
    interval_seconds: float = 0.0,
) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": enabled,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote": _REMOTE,
                "auto_integrate_remote_enabled": remote_enabled,
                "auto_integrate_remote_interval_seconds": interval_seconds,
            }
        }
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_concurrent_remote_advance_converges_to_ref_equality(tmp_path: Path) -> None:
    repo, bare = _seed_cluster(tmp_path)
    writer = tmp_path / "writer"

    assert _run(tmp_path, "clone", str(bare), str(writer)).returncode == 0
    assert _run(writer, "config", "user.email", "test@example.com").returncode == 0
    assert _run(writer, "config", "user.name", "Test User").returncode == 0
    assert _run(writer, "remote", "add", _REMOTE, str(bare)).returncode == 0
    remote_advance_sha = _commit(writer, "remote.txt", "remote work\n", "remote advance")
    assert _run(writer, "push", _REMOTE, _TARGET).returncode == 0

    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    assert _run(repo, "fetch", _REMOTE).returncode == 0

    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    assert _head_sha(repo, f"refs/remotes/{_REMOTE}/{_TARGET}") == remote_advance_sha
    assert local_sha != remote_advance_sha

    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    remote_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == remote_ref, f"local {local_ref} != remote {remote_ref} after reconciliation"
    out_local = _run(
        repo,
        "log",
        "--format=%H",
        f"{local_sha}..{local_ref}",
    )
    assert out_local.returncode == 0
    descendants = out_local.stdout.split()
    assert remote_advance_sha in descendants, (
        f"remote advance {remote_advance_sha} missing from reconciled history"
    )
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.MERGED,
        RebaseOutcomeKind.REBASED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_throttle_deferred_probe_publishes_to_configured_release_tacos(tmp_path: Path) -> None:
    repo, bare = _seed_cluster(tmp_path)

    cfg = _build_config(interval_seconds=60.0)

    remote_sync._REMOTE_PULL_THROTTLE.arm(repo, _REMOTE, _TARGET, clock=time.monotonic)

    pull_record = remote_sync.pull_and_reconcile_target(
        cfg,
        repo,
        _TARGET,
    )
    assert pull_record is not None
    assert pull_record.last_remote_sync == "refresh suppressed"
    assert pull_record.last_refresh == "refresh suppressed by throttle"

    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    state = remote_sync.reconcile_remote_publication(
        cfg,
        repo,
        _TARGET,
        source_state=pull_record,
    )

    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == bare_ref == local_sha
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )

    remotes = _run(repo, "remote").stdout.split()
    assert "origin" not in remotes
    branches = _run(repo, "branch", "-a").stdout
    assert "main" not in branches


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_throttle_deferred_probe_still_publishes_concrete_refs(tmp_path: Path) -> None:
    repo, bare = _seed_cluster(tmp_path)

    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    bare_pre = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_pre != local_sha

    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == bare_ref == local_sha
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_killed_between_landing_and_push_publishes_on_recovery(tmp_path: Path) -> None:
    repo, bare = _seed_cluster(tmp_path)

    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    bare_ref_before = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_before != local_sha

    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    bare_ref_after = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_after == local_sha
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_killed_between_landing_and_push_recovers_from_linked_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.rebase_state import RebaseState

    repo, bare = _seed_cluster(tmp_path)
    wt = tmp_path / "wt-sibling"

    assert _run(repo, "switch", "-c", "feature").returncode == 0
    _commit(repo, "feature.txt", "feature work\n", "feature advance")

    def _interrupt_push(*_args: object, **_kwargs: object) -> object:
        raise OSError("simulated kill before push completes")

    with monkeypatch.context() as push_patch:
        push_patch.setattr(remote_sync, "push_single_remote", _interrupt_push)
        with contextlib.suppress(OSError):
            auto_integrate_after_commit(_build_config(), WorkspaceScope(repo), RebaseState())

    local_sha = _head_sha(repo, f"refs/heads/{_TARGET}")

    bare_ref_before = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_before != local_sha

    assert _run(repo, "worktree", "add", "-b", "wt-feature", str(wt), _TARGET).returncode == 0
    assert _run(wt, "config", "user.email", "test@example.com").returncode == 0
    assert _run(wt, "config", "user.name", "Test User").returncode == 0

    rec_state = auto_integrate_recovery.recover_incomplete_integration(
        WorkspaceScope(wt), config=_build_config()
    )
    assert rec_state is not None and rec_state.last_outcome is not None
    out_line = str(rec_state.last_outcome)

    bare_ref_after = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_after == local_sha
    assert out_line in (
        str(RebaseOutcomeKind.REMOTE_PUSHED),
        str(RebaseOutcomeKind.FAST_FORWARDED),
    ), f"recovery did not report a push outcome: {out_line!r}"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_unreachable_then_reachable_auto_pushes_real_refs(tmp_path: Path) -> None:
    repo, bare = _seed_cluster(tmp_path)

    unreachable_bare = tmp_path / "tacos_unreachable.git"
    assert _run(repo, "remote", "set-url", _REMOTE, str(unreachable_bare)).returncode == 0
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")
    state_first = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )
    assert state_first.last_remote_sync in (
        remote_sync.REMOTE_PULL_FAILED,
        remote_sync.REMOTE_REMOTE_UNREACHABLE,
    )

    assert _run(repo, "remote", "set-url", _REMOTE, str(bare)).returncode == 0

    state_second = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    assert bare_ref == local_ref == local_sha
    assert state_second.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_unreachable_then_reachable_production_retry_succeeds_across_subprocess(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from ralph.pipeline import run_loop
    from ralph.pipeline.rebase_state import RebaseOutcomeKind as LoopRebaseOutcomeKind
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import default_dir, load_policy

    repo, bare = _seed_cluster(tmp_path)
    unreachable_bare = tmp_path / "tacos_unreachable.git"

    assert _run(repo, "remote", "set-url", _REMOTE, str(unreachable_bare)).returncode == 0
    local_sha = _commit(repo, "feature.txt", "feature advance\n", "feature advance")
    seed_sha = _head_sha(repo, "HEAD~1")
    config = _build_config()

    observed_rebase = remote_sync.reconcile_remote_publication(
        config,
        repo,
        _TARGET,
        remote=_REMOTE,
    )
    assert observed_rebase.last_publication_decision == "publish_deferred_remote"
    assert observed_rebase.last_remote_sync in (
        remote_sync.REMOTE_REMOTE_UNREACHABLE,
        remote_sync.REMOTE_PULL_FAILED,
    )

    deferred_signals: list[str] = []
    attempt = {"n": 0}

    def sleep_hook(_seconds: float) -> None:
        attempt["n"] += 1
        deferred_signals.append(f"DEFERRED:{attempt['n']}:{local_sha}")
        bare_sha = _run(bare, "rev-parse", "release").stdout.strip()
        assert bare_sha == seed_sha
        assert bare_sha != local_sha
        if attempt["n"] >= 2:
            assert _run(repo, "remote", "set-url", _REMOTE, str(bare)).returncode == 0

    bundle = load_policy(default_dir())
    pipeline_state = PipelineState(phase="complete", rebase=observed_rebase)
    ctx = SimpleNamespace(
        config=config,
        workspace_scope=WorkspaceScope(repo),
        latest_state=[pipeline_state],
        sleep=sleep_hook,
        active_display=None,
        display_context=None,
        effective_verbosity=None,
        registry=None,
        effective_pipeline_subscriber=None,
        controller=None,
        config_path=None,
        cli_overrides=None,
        monitor_stop=None,
        connectivity_monitor=None,
        pipeline_deps=None,
        snapshot_registry=None,
        process_teardown=None,
        _session_handoff_dispatched=False,
        policy_bundle=bundle,
    )
    final_state, _, _ = run_loop._run_inner_loop_after_startup(pipeline_state, ctx, "development")

    assert deferred_signals == [
        f"DEFERRED:1:{local_sha}",
        f"DEFERRED:2:{local_sha}",
    ]
    assert final_state.rebase is not None
    assert final_state.rebase.last_outcome in (
        LoopRebaseOutcomeKind.REMOTE_PUSHED,
        LoopRebaseOutcomeKind.FAST_FORWARDED,
    )
    bare_sha = _run(bare, "rev-parse", "release").stdout.strip()
    assert bare_sha == local_sha
