from __future__ import annotations

import contextlib
import shutil
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
from git import Commit, Repo, Tree
from git.exc import GitError

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import auto_integrate, auto_integrate_sync
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit as _auto_integrate_after_commit,
)
from ralph.pipeline.auto_integrate_ff import is_retryable_fast_forward_failure
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
    refresh_target_from_remote,
)
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseState
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope


class _FastClock(FakeClock):
    def now(self) -> float:
        return self.monotonic()


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "session-scoped remote-refresh topology templates": (
        "each real-git case still starts from the same bare origin, agent feature checkout, "
        "optional main owner worktree, and remote tip; only the immutable template is built "
        "once per session and copytree'd per test"
    ),
    "in-process object publish for remote advances": (
        "remote tip and blob contents still advance on the bare origin exactly as a push would; "
        "only the transport copies objects and updates the ref without a git push subprocess"
    ),
    "FakeClock recovery controller on auto_integrate_after_commit": (
        "observable rebase, ancestry, dirty-owner, and remote-sync outcomes are unchanged; "
        "only wall-clock sleeps and policy-bundle controller construction are replaced"
    ),
    "GitPython reads for porcelain, log subject, and ancestry assertions": (
        "assertions still compare the same clean porcelain, commit subjects, and ancestor "
        "relationships; only the read transport moved off the git executable"
    ),
}

_CONCURRENT_MOVE = "target advanced concurrently (CAS mismatch)"
_TARGET = "main"
_FEATURE = "feature"
_REPO_CACHE: dict[Path, Repo] = {}
_TEMPLATES: dict[str, Path] = {}


def auto_integrate_after_commit(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    **kwargs: object,
) -> RebaseState | None:
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return _auto_integrate_after_commit(config, workspace_scope, state, **kwargs)


def _auto_integrate_with_fake_clock(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    **kwargs: object,
) -> RebaseState | None:
    clock = _FastClock(start=0.0)
    kwargs.setdefault("sleep", clock.sleep)
    kwargs.setdefault("jitter", lambda: 0.0)
    kwargs.setdefault(
        "recovery_controller",
        RecoveryController(options=RecoveryControllerOptions(clock=clock)),
    )
    token = install_executors(clock=clock)
    try:
        return _auto_integrate_after_commit(config, workspace_scope, state, **kwargs)
    finally:
        reset_executors(token)


def _repo(path: Path) -> Repo | None:
    cached = _REPO_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        repo = Repo(path)
    except (GitError, OSError, ValueError, KeyError, AttributeError):
        return None
    _REPO_CACHE[path] = repo
    return repo


def _refreshed_repo(path: Path) -> Repo | None:
    stale = _REPO_CACHE.pop(path, None)
    if stale is not None:
        with contextlib.suppress(GitError, OSError, ValueError):
            stale.close()
    try:
        repo = Repo(path)
    except (GitError, OSError, ValueError, KeyError, AttributeError):
        return None
    _REPO_CACHE[path] = repo
    return repo


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _identify(path: Path) -> None:
    repo = _refreshed_repo(path)
    assert repo is not None
    writer = repo.config_writer()
    try:
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("user", "name", "Test User")
    finally:
        writer.release()


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo = _refreshed_repo(repo_root)
    assert repo is not None
    repo.index.add([filename])
    return repo.index.commit(message).hexsha


def _tree_shas(tree: Tree) -> list[str]:
    shas = [tree.hexsha]
    for entry in tree:
        if isinstance(entry, Tree):
            shas.extend(_tree_shas(entry))
        else:
            shas.append(entry.hexsha)
    return shas


def _copy_objects(source: Repo, target: Repo, commit: Commit) -> None:
    source.git.clear_cache()
    target.git.clear_cache()
    source_objects = Path(source.git_dir) / "objects"
    target_objects = Path(target.git_dir) / "objects"
    for sha in [commit.hexsha, *_tree_shas(commit.tree)]:
        name = f"{sha[:2]}/{sha[2:]}"
        destination = target_objects / name
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_objects / name, destination)
    source.git.clear_cache()
    target.git.clear_cache()


def _set_ref(git_dir: Path, ref: str, sha: str) -> None:
    cached = _REPO_CACHE.pop(git_dir, None)
    if cached is not None:
        cached.close()
    path = git_dir / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{sha}\n", encoding="utf-8")


def _publish_commit(writer: Path, bare: Path, filename: str, content: str, message: str) -> str:
    sha = _commit(writer, filename, content, message)
    writer_repo = _refreshed_repo(writer)
    bare_repo = _refreshed_repo(bare)
    assert writer_repo is not None and bare_repo is not None
    _copy_objects(writer_repo, bare_repo, writer_repo.commit(sha))
    _set_ref(bare, f"refs/heads/{_TARGET}", sha)
    return sha


def _porcelain(path: Path) -> str:
    repo = _repo(path)
    assert repo is not None
    return repo.git.status("--porcelain")


def _log_subject(path: Path) -> str:
    repo = _repo(path)
    assert repo is not None
    return str(repo.head.commit.message).splitlines()[0]


def _is_ancestor(path: Path, ancestor: str, descendant: str) -> bool:
    repo = _repo(path)
    assert repo is not None
    try:
        return repo.is_ancestor(repo.commit(ancestor), repo.commit(descendant))
    except (GitError, OSError, ValueError, KeyError, AttributeError):
        return _git(path, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def _build_config(*, remote_enabled: bool = False, target: str | None = None) -> UnifiedConfig:
    general: dict[str, object] = {
        "auto_integrate_enabled": True,
        "auto_integrate_remote_enabled": remote_enabled,
    }
    if target is not None:
        general["auto_integrate_target"] = target
    return UnifiedConfig.model_validate({"general": general})


def _ignore_inert_git_samples(directory: str, names: list[str]) -> set[str]:
    del names
    name = Path(directory).name
    return {"hooks"} if name == ".git" or name.endswith(".git") else set()


def _rebase_paths(source: Path, target: Path) -> None:
    old = str(source).encode()
    new = str(target).encode()
    candidates = [
        target / "agent" / ".git" / "config",
        target / "writer" / ".git" / "config",
        target / "origin.git" / "config",
        target / "owner" / ".git",
        *(target / "agent" / ".git" / "worktrees").glob("*/gitdir"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        data = path.read_bytes()
        patched = data.replace(old, new)
        if patched != data:
            path.write_bytes(patched)


@dataclass(frozen=True)
class _Topology:
    bare: Path
    agent: Path
    owner: Path
    writer: Path
    remote_sha: str


def _seed_topology(root: Path) -> tuple[Path, Path, Path, Path]:
    seed = root / "seed"
    seed_repo = Repo.init(seed, initial_branch=_TARGET)
    try:
        _identify(seed)
        (seed / "seed.txt").write_text("seed\n", encoding="utf-8")
        seed_repo.index.add(["seed.txt"])
        seed_repo.index.commit("seed")
    finally:
        seed_repo.close()
    bare = root / "origin.git"
    assert _git(root, "clone", "-q", "--bare", str(seed), str(bare)).returncode == 0
    writer = root / "writer"
    assert _git(root, "clone", "-q", str(bare), str(writer)).returncode == 0
    _identify(writer)
    agent = root / "agent"
    assert _git(root, "clone", "-q", str(bare), str(agent)).returncode == 0
    _identify(agent)
    exclude = agent / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(".agent/\n", encoding="utf-8")
    assert _git(agent, "checkout", "-q", "-b", _FEATURE).returncode == 0
    owner = root / "owner"
    assert _git(agent, "worktree", "add", "-q", str(owner), _TARGET).returncode == 0
    return bare, agent, owner, writer


def _build_ahead_template(root: Path) -> None:
    bare, _agent, _owner, writer = _seed_topology(root)
    _publish_commit(writer, bare, "remote.txt", "remote advance\n", "remote advance")


def _build_conflict_template(root: Path) -> None:
    bare, agent, owner, writer = _seed_topology(root)
    _commit(owner, "shared.txt", "local target\n", "local target change")
    _commit(agent, "feature.txt", "feature\n", "feature change")
    _publish_commit(writer, bare, "shared.txt", "remote target\n", "remote target change")


def _build_clean_template(root: Path) -> None:
    bare, agent, owner, writer = _seed_topology(root)
    _commit(owner, "local.txt", "local target\n", "local target change")
    _commit(agent, "feature.txt", "feature\n", "feature change")
    _publish_commit(writer, bare, "remote.txt", "remote target\n", "remote target change")


@pytest.fixture(autouse=True)
def _close_cached_repos() -> Generator[None, None, None]:
    yield
    for repo in _REPO_CACHE.values():
        with contextlib.suppress(GitError, OSError, ValueError):
            repo.close()
    _REPO_CACHE.clear()


@pytest.fixture(scope="session", autouse=True)
def _scenario_templates(tmp_path_factory: pytest.TempPathFactory) -> None:
    builders = {
        "ahead": _build_ahead_template,
        "conflict": _build_conflict_template,
        "clean": _build_clean_template,
    }
    for name, builder in builders.items():
        root = tmp_path_factory.mktemp(f"remote-refresh-{name}")
        builder(root)
        _TEMPLATES[name] = root


def _materialize(tmp_path: Path, variant: str) -> _Topology:
    template = _TEMPLATES[variant]
    root = tmp_path / f"scenario-{variant}"
    shutil.copytree(template, root, ignore=_ignore_inert_git_samples)
    _rebase_paths(template, root)
    bare = root / "origin.git"
    remote_sha = (bare / "refs" / "heads" / _TARGET).read_text(encoding="utf-8").strip()
    return _Topology(
        bare=bare,
        agent=root / "agent",
        owner=root / "owner",
        writer=root / "writer",
        remote_sha=remote_sha,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_ahead_refresh_keeps_the_local_target_unchanged(tmp_path: Path) -> None:
    topo = _materialize(tmp_path, "ahead")
    local_main = branch_sha(topo.agent, _TARGET)
    assert local_main is not None
    assert topo.remote_sha != local_main

    outcome = refresh_target_from_remote(topo.agent, _TARGET, timeout_seconds=2.0)

    assert outcome == REFRESH_ORIGIN_AHEAD
    assert branch_sha(topo.agent, _TARGET) == local_main
    assert not (topo.agent / "remote.txt").exists()


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_divergence_regression_resolver_reconciles_target_then_lands_feature(
    tmp_path: Path,
) -> None:
    topo = _materialize(tmp_path, "conflict")

    def resolve_target(root: Path, _target: str, _stop: object) -> bool:
        (root / "shared.txt").write_text("resolved target\n", encoding="utf-8")
        return True

    outcome = _auto_integrate_with_fake_clock(
        _build_config(remote_enabled=True, target=_TARGET),
        WorkspaceScope(topo.agent),
        RebaseState(),
        rebase_stop_resolver=resolve_target,
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert _is_ancestor(topo.agent, topo.remote_sha, _TARGET) is True
    assert _is_ancestor(topo.agent, topo.remote_sha, f"origin/{_TARGET}") is True
    assert (topo.owner / "shared.txt").read_text(encoding="utf-8") == "resolved target\n"
    assert _porcelain(topo.agent) == ""
    assert _porcelain(topo.owner) == ""
    assert rebase_in_progress(topo.agent) is False
    assert rebase_in_progress(topo.owner) is False


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_divergence_regression_clean_abort_conflict_still_lands_feature_locally(
    tmp_path: Path,
) -> None:
    topo = _materialize(tmp_path, "conflict")

    outcome = _auto_integrate_with_fake_clock(
        _build_config(remote_enabled=True, target=_TARGET),
        WorkspaceScope(topo.agent),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert _is_ancestor(topo.agent, topo.remote_sha, _TARGET) is False
    assert _log_subject(topo.agent) == "feature change"
    assert _porcelain(topo.agent) == ""
    assert _porcelain(topo.owner) == ""
    assert rebase_in_progress(topo.agent) is False
    assert rebase_in_progress(topo.owner) is False


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_divergence_regression_reclaims_dirty_owner_then_lands_feature_after_clean_abort(
    tmp_path: Path,
) -> None:
    topo = _materialize(tmp_path, "conflict")
    operator_file = topo.owner / "operator.txt"
    operator_file.write_text("operator work\n", encoding="utf-8")

    outcome = _auto_integrate_with_fake_clock(
        _build_config(remote_enabled=True, target=_TARGET),
        WorkspaceScope(topo.agent),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert outcome.last_remote_sync == "pull failed"
    assert _is_ancestor(topo.agent, topo.remote_sha, _TARGET) is False
    assert _porcelain(topo.agent) == ""
    assert operator_file.read_text(encoding="utf-8") == "operator work\n"
    assert rebase_in_progress(topo.agent) is False
    assert rebase_in_progress(topo.owner) is False


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_divergence_regression_rebases_target_and_lands_feature(
    tmp_path: Path,
) -> None:
    topo = _materialize(tmp_path, "clean")

    outcome = _auto_integrate_with_fake_clock(
        _build_config(remote_enabled=True, target=_TARGET),
        WorkspaceScope(topo.agent),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert _is_ancestor(topo.agent, topo.remote_sha, _TARGET) is True
    assert _is_ancestor(topo.agent, topo.remote_sha, f"origin/{_TARGET}") is True
    assert _porcelain(topo.agent) == ""
    assert _porcelain(topo.owner) == ""
    assert rebase_in_progress(topo.agent) is False
    assert rebase_in_progress(topo.owner) is False


def test_unreachable_remote_degrades_to_local_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auto_integrate_sync, "_has_remote", lambda _root, _remote="origin": True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout, *, remote=None: False,
    )

    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_UNREACHABLE
    )


@pytest.mark.subprocess_e2e
def test_a_remote_fetch_that_times_out_reports_the_remote_unreachable(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        with repo.config_writer() as writer:
            writer.set_value('remote "origin"', "url", "ext::sh -c sleep% 30")
            writer.set_value('remote "origin"', "fetch", "+refs/heads/*:refs/remotes/origin/*")
            writer.set_value("protocol", "allow", "always")
    finally:
        repo.close()

    outcome = refresh_target_from_remote(
        tmp_git_repo,
        "main",
        timeout_seconds=0.2,
        remote="origin",
    )

    assert outcome == REFRESH_UNREACHABLE


@pytest.mark.parametrize("seam", ("remote lookup", "classification"))
def test_refresh_regression_git_observation_failure_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    if seam == "remote lookup":
        monkeypatch.setattr(
            auto_integrate_sync,
            "_has_remote",
            lambda *_args: (_ for _ in ()).throw(OSError("remote lookup failed")),
        )
    else:
        monkeypatch.setattr(auto_integrate_sync, "_has_remote", lambda *_args: True)
        monkeypatch.setattr(auto_integrate_sync, "_fetch_target", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(
            auto_integrate_sync,
            "_classify_remote_position",
            lambda *_args: (_ for _ in ()).throw(OSError("ref query failed")),
        )

    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_UNREACHABLE
    )


def _inject_remote_position(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ancestor: bool,
) -> None:
    monkeypatch.setattr(auto_integrate_sync, "_has_remote", lambda _root, _remote="origin": True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout, *, remote=None: True,
    )
    monkeypatch.setattr(
        auto_integrate_sync,
        "_remote_tracking_sha",
        lambda _root, _target, _remote="origin": "remote",
    )
    monkeypatch.setattr(auto_integrate_sync, "branch_sha", lambda _root, _target: "local")
    monkeypatch.setattr(
        auto_integrate_sync,
        "is_ancestor",
        lambda _root, _ancestor, _descendant: ancestor,
    )


def test_diverged_remote_is_not_force_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inject_remote_position(monkeypatch, ancestor=False)
    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_DIVERGED
    )


def test_retry_attempt_refetches_and_reclassifies_the_remote_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert is_retryable_fast_forward_failure(_CONCURRENT_MOVE) is True

    root = Path("/workspace")
    events: list[str] = []
    retries = iter((True, False))
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_resolve_context",
        lambda _config, _scope: (root, "feature", "main", "origin ahead"),
    )
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_check_skip_conditions",
        lambda _root, _branch, _target: None,
    )
    monkeypatch.setattr(
        auto_integrate, "observe_conflict_identity", lambda _root, _target: "identity"
    )
    monkeypatch.setattr(
        auto_integrate,
        "configured_resolver_allowed",
        lambda _state, _target, _identity, **_kwargs: True,
    )
    monkeypatch.setattr(
        auto_integrate,
        "pull_and_reconcile_target",
        lambda *_args, **_kwargs: (
            events.append("remote refresh")
            or RebaseState(
                last_remote_sync="already current",
                freshness_verdict="verified",
                freshness_source="fetch",
            )
        ),
    )

    def _integrate_once(*_args: object, **_kwargs: object) -> tuple[RebaseState, bool]:
        events.append("integrate")
        return (
            RebaseState(
                last_action="rebased",
                last_target="main",
                fast_forwarded=True,
            ),
            next(retries),
        )

    monkeypatch.setattr(auto_integrate, "_integrate_once", _integrate_once)
    outcome = auto_integrate_after_commit(
        _build_config(remote_enabled=True),
        WorkspaceScope(root),
        RebaseState(),
        sleep=lambda _seconds: events.append("backoff"),
        jitter=lambda: 0.0,
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert events == ["remote refresh", "integrate", "backoff", "remote refresh", "integrate"]


def test_refresh_observation_never_moves_the_local_ref_even_when_origin_is_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inject_remote_position(monkeypatch, ancestor=True)
    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_ORIGIN_AHEAD
    )


def test_refresh_regression_failed_fetch_never_claims_a_fresh_origin_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auto_integrate_sync, "_has_remote", lambda _root, _remote="origin": True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout, *, remote=None: False,
    )
    monkeypatch.setattr(
        auto_integrate_sync,
        "_classify_remote_position",
        lambda _root, _target: pytest.fail("used a stale remote-tracking ref"),
    )

    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_UNREACHABLE
    )
