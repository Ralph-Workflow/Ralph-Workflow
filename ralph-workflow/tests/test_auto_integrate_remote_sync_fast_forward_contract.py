from __future__ import annotations

import contextlib
import shutil
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from itertools import count
from pathlib import Path

import pytest
from git import Commit, Repo, Tree

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_catchup import (
    AgentPhaseActivity,
    AutoIntegrateCatchupWorker,
    start_catchup_worker_if_enabled,
)
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.auto_integrate_remote_constants import RemoteSyncStatus
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_TARGET = "main"
_FEATURE = "feature"
_WORKTREE = "wt-001-feature"

_TEMPLATES: dict[str, Path] = {}

_VARIANTS: dict[str, tuple[str, bool]] = {
    "origin-plain": ("origin", False),
    "origin-feature": ("origin", True),
    "tacos-feature": ("tacos", True),
}


_REPO_CACHE: dict[Path, Repo] = {}

_SCENARIO_SEQUENCE = count()


def _repo(path: Path) -> Repo | None:
    cached = _REPO_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        repo = Repo(path)
    except Exception:
        return None
    _REPO_CACHE[path] = repo
    return repo


def _refreshed_repo(path: Path) -> Repo | None:
    stale = _REPO_CACHE.pop(path, None)
    if stale is not None:
        with contextlib.suppress(Exception):
            stale.close()
    try:
        repo = Repo(path)
    except Exception:
        return None
    _REPO_CACHE[path] = repo
    return repo


def _in_process_git(cwd: Path, args: tuple[str, ...]) -> str | None:
    value: str | None = None
    for repo in (_repo(cwd), _refreshed_repo(cwd)):
        if repo is None:
            continue
        try:
            if len(args) == 2 and args[0] == "rev-parse":
                value = repo.rev_parse(args[1]).hexsha
            elif len(args) == 2 and args[0] == "symbolic-ref":
                value = str(repo.head.ref)
            elif (
                len(args) == 4 and args[0] == "log" and args[1] == "-1" and args[2] == "--format=%s"
            ):
                value = str(repo.commit(args[3]).message).splitlines()[0]
            elif len(args) == 3 and args[0] == "log" and args[1] == "--format=%s":
                value = "\n".join(
                    str(commit.message).splitlines()[0] for commit in repo.iter_commits(args[2])
                )
            elif args == ("remote",):
                value = "\n".join(remote.name for remote in repo.remotes)
            else:
                return None
        except Exception:
            continue
        return value
    return None


def _git(cwd: Path, *args: str) -> str:
    in_process = _in_process_git(cwd, args)
    if in_process is not None:
        return in_process
    result = subprocess.run(
        ("git", *args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, f"git {args} failed in {cwd}: {result.stderr}"
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    git_repo = _refreshed_repo(repo)
    assert git_repo is not None
    git_repo.index.add([filename])
    return git_repo.index.commit(message).hexsha


def _identify(repo: Path) -> None:
    git_repo = _refreshed_repo(repo)
    assert git_repo is not None
    writer = git_repo.config_writer()
    try:
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("user", "name", "Test User")
    finally:
        writer.release()


def _publish_commit(writer: Path, bare: Path, filename: str, content: str, message: str) -> str:
    (writer / filename).write_text(content, encoding="utf-8")
    writer_repo = _refreshed_repo(writer)
    bare_repo = _refreshed_repo(bare)
    assert writer_repo is not None and bare_repo is not None
    writer_repo.index.add([filename])
    commit = writer_repo.index.commit(message)
    _copy_objects(writer_repo, bare_repo, commit)
    _set_ref(bare, f"refs/heads/{_TARGET}", commit.hexsha)
    return commit.hexsha


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


def _tree_shas(tree: Tree) -> list[str]:
    shas = [tree.hexsha]
    for entry in tree:
        if isinstance(entry, Tree):
            shas.extend(_tree_shas(entry))
        else:
            shas.append(entry.hexsha)
    return shas


def _set_ref(git_dir: Path, ref: str, sha: str) -> None:
    cached = _REPO_CACHE.pop(git_dir, None)
    if cached is not None:
        cached.close()
    path = git_dir / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{sha}\n", encoding="utf-8")


def _ref_sha(git_dir: Path, name: str) -> str:
    ref = f"refs/heads/{name}"
    loose = git_dir / "refs" / "heads" / name
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            sha, _, ref_name = line.partition(" ")
            if ref_name == ref:
                return sha
    return _git(git_dir, "rev-parse", ref)


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    git_repo = _repo(repo)
    assert git_repo is not None
    try:
        return git_repo.is_ancestor(git_repo.commit(ancestor), git_repo.commit(descendant))
    except Exception:
        result = subprocess.run(
            ("git", "merge-base", "--is-ancestor", ancestor, descendant),
            cwd=str(repo),
            capture_output=True,
            check=False,
            timeout=20,
        )
        return result.returncode == 0


@dataclass(frozen=True)
class _Topology:
    bare: Path
    clone: Path
    feature: Path
    writer: Path
    remote: str

    def ref(self, name: str) -> str:
        return _ref_sha(self.clone / ".git", name)

    def origin_tip(self) -> str:
        return _ref_sha(self.bare, _TARGET)

    def push_remote_advance(self, filename: str = "remote.txt") -> str:
        if _ref_sha(self.writer / ".git", _TARGET) != _ref_sha(self.bare, _TARGET):
            _git(self.writer, "pull", "-q", "--ff-only", self.remote, _TARGET)
        return _publish_commit(
            self.writer, self.bare, filename, f"{filename}\n", f"remote advance {filename}"
        )


def _ignore_inert_git_samples(directory: str, names: list[str]) -> set[str]:
    del names
    name = Path(directory).name
    return {"hooks"} if name == ".git" or name.endswith(".git") else set()


def _rebase_paths(source: Path, target: Path) -> None:
    old = str(source).encode()
    new = str(target).encode()
    candidates = [
        target / "clone" / ".git" / "config",
        target / "writer" / ".git" / "config",
        target / "origin.git" / "config",
        target / _WORKTREE / ".git",
        *(target / "clone" / ".git" / "worktrees").glob("*/gitdir"),
    ]
    for path in candidates:
        data = path.read_bytes()
        patched = data.replace(old, new)
        if patched != data:
            path.write_bytes(patched)


def _build_template(root: Path, *, remote: str, feature_commit: bool) -> None:
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
    _git(root, "clone", "-q", "--bare", str(seed), str(bare))
    clone = root / "clone"
    _git(root, "clone", "-q", "-o", remote, str(bare), str(clone))
    _identify(clone)
    (clone / ".git" / "info" / "exclude").write_text(".agent/\n", encoding="utf-8")
    feature = root / _WORKTREE
    _git(clone, "worktree", "add", "-q", "-b", _FEATURE, str(feature), _TARGET)
    if feature_commit:
        _commit(feature, "feature.txt", "feature\n", "feature work")
    writer = root / "writer"
    _git(root, "clone", "-q", "-o", remote, str(bare), str(writer))
    _identify(writer)


@pytest.fixture(autouse=True)
def _close_cached_repos() -> Generator[None, None, None]:
    yield
    for repo in _REPO_CACHE.values():
        repo.close()
    _REPO_CACHE.clear()


@pytest.fixture(scope="session", autouse=True)
def _scenario_templates(tmp_path_factory: pytest.TempPathFactory) -> None:
    for variant, (remote, feature_commit) in _VARIANTS.items():
        root = tmp_path_factory.mktemp(f"remote-sync-{variant}")
        _build_template(root, remote=remote, feature_commit=feature_commit)
        _TEMPLATES[variant] = root


def _build(
    tmp_path: Path,
    *,
    remote: str = "origin",
    feature_commit: bool = True,
) -> _Topology:
    variant = f"{remote}-{'feature' if feature_commit else 'plain'}"
    template = _TEMPLATES[variant]
    root = tmp_path / f"scenario-{next(_SCENARIO_SEQUENCE)}"
    shutil.copytree(template, root, ignore=_ignore_inert_git_samples)
    _rebase_paths(template, root)
    return _Topology(
        bare=root / "origin.git",
        clone=root / "clone",
        feature=root / _WORKTREE,
        writer=root / "writer",
        remote=remote,
    )


def _config(*, remote: str = "origin", remote_enabled: bool = True) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote_enabled": remote_enabled,
                "auto_integrate_remote": remote,
                "auto_integrate_remote_interval_seconds": 0.0,
            }
        }
    )


def _post_commit(config: UnifiedConfig, root: Path) -> RebaseState | None:
    return auto_integrate_after_commit(
        config,
        WorkspaceScope(root),
        RebaseState(),
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )


def _run_ticks(
    config: UnifiedConfig,
    root: Path,
    ticks: int = 1,
    *,
    agent_phases: AgentPhaseActivity | None = None,
) -> None:
    stops = iter([False] * ticks + [True])
    worker = AutoIntegrateCatchupWorker(
        config, root, wait=lambda _interval: next(stops), agent_phases=agent_phases
    )
    worker.run()


def test_post_commit_from_feature_worktree_pulls_origin_then_lands_feature(
    tmp_path: Path,
) -> None:
    topo = _build(tmp_path)
    remote_sha = topo.push_remote_advance()

    outcome = _post_commit(_config(), topo.feature)

    assert outcome is not None
    main = topo.ref(_TARGET)
    assert _is_ancestor(topo.clone, remote_sha, main)
    assert _git(topo.clone, "log", "-1", "--format=%s", main) == "feature work"
    assert topo.origin_tip() == main
    assert (topo.clone / "remote.txt").exists()
    assert _git(topo.clone, "status", "--porcelain") == ""


def test_post_commit_on_target_branch_fast_forwards_main_to_origin(tmp_path: Path) -> None:
    topo = _build(tmp_path, feature_commit=False)
    remote_sha = topo.push_remote_advance()

    _post_commit(_config(), topo.clone)

    assert topo.ref(_TARGET) == remote_sha
    assert (topo.clone / "remote.txt").read_text(encoding="utf-8") == "remote.txt\n"
    assert _git(topo.clone, "status", "--porcelain") == ""


def test_post_commit_on_target_branch_publishes_and_reconciles_local_commits(
    tmp_path: Path,
) -> None:
    topo = _build(tmp_path, feature_commit=False)
    remote_sha = topo.push_remote_advance()
    local_sha = _commit(topo.clone, "local.txt", "local\n", "local trunk commit")

    _post_commit(_config(), topo.clone)

    main = topo.ref(_TARGET)
    assert _is_ancestor(topo.clone, remote_sha, main)
    assert "local trunk commit" in _git(topo.clone, "log", "--format=%s", main)
    assert main != local_sha
    assert topo.origin_tip() == main


def test_post_commit_with_feature_at_target_fast_forwards_main_then_ticker_feature(
    tmp_path: Path,
) -> None:
    topo = _build(tmp_path, feature_commit=False)
    remote_sha = topo.push_remote_advance()

    _post_commit(_config(), topo.feature)

    assert topo.ref(_TARGET) == remote_sha

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_FEATURE) == remote_sha
    assert (topo.feature / "remote.txt").exists()


def test_post_commit_on_dirty_target_checkout_fails_closed(tmp_path: Path) -> None:
    topo = _build(tmp_path, feature_commit=False)
    before = topo.ref(_TARGET)
    topo.push_remote_advance()
    (topo.clone / "seed.txt").write_text("uncommitted edit\n", encoding="utf-8")

    _post_commit(_config(), topo.clone)

    assert topo.ref(_TARGET) == before
    assert (topo.clone / "seed.txt").read_text(encoding="utf-8") == "uncommitted edit\n"
    assert _git(topo.clone, "stash", "list") == ""


def test_post_commit_uses_the_configured_non_default_remote(tmp_path: Path) -> None:
    topo = _build(tmp_path, remote="tacos")
    remote_sha = topo.push_remote_advance()

    _post_commit(_config(remote="tacos"), topo.feature)

    main = topo.ref(_TARGET)
    assert _is_ancestor(topo.clone, remote_sha, main)
    assert topo.origin_tip() == main


def test_start_catchup_worker_runs_a_ticker_when_remote_sync_is_enabled(
    tmp_path: Path,
) -> None:
    topo = _build(tmp_path)
    worker = start_catchup_worker_if_enabled(_config(), topo.feature, interval_seconds=3600.0)
    try:
        assert worker is not None
        assert worker.is_running
    finally:
        if worker is not None:
            worker.stop()


def test_ticker_fast_forwards_main_checked_out_in_another_worktree(tmp_path: Path) -> None:
    topo = _build(tmp_path)
    remote_sha = topo.push_remote_advance()

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == remote_sha
    assert (topo.clone / "remote.txt").exists()
    assert _git(topo.clone, "status", "--porcelain") == ""


def test_ticker_fast_forwards_target_when_run_checkout_is_on_target(tmp_path: Path) -> None:
    topo = _build(tmp_path, feature_commit=False)
    remote_sha = topo.push_remote_advance()

    _run_ticks(_config(), topo.clone)

    assert topo.ref(_TARGET) == remote_sha
    assert (topo.clone / "remote.txt").exists()


def test_ticker_applies_a_remote_advance_inside_the_fetch_cadence(tmp_path: Path) -> None:
    topo = _build(tmp_path)
    topo.push_remote_advance()
    _run_ticks(_config(), topo.feature)
    second = topo.push_remote_advance("remote2.txt")

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == second
    assert (topo.clone / "remote2.txt").exists()


def test_ticker_refuses_dirty_target_owner_then_retries_once_clean(tmp_path: Path) -> None:
    topo = _build(tmp_path)
    before = topo.ref(_TARGET)
    remote_sha = topo.push_remote_advance()
    (topo.clone / "seed.txt").write_text("uncommitted edit\n", encoding="utf-8")

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == before
    assert (topo.clone / "seed.txt").read_text(encoding="utf-8") == "uncommitted edit\n"

    (topo.clone / "seed.txt").write_text("seed\n", encoding="utf-8")
    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == remote_sha


def test_ticker_never_force_moves_a_conflicting_diverged_target(tmp_path: Path) -> None:
    topo = _build(tmp_path)
    origin_before = topo.push_remote_advance()
    local_sha = _commit(topo.clone, "remote.txt", "local version\n", "local conflicting")

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == local_sha
    assert topo.origin_tip() == origin_before
    assert not (topo.clone / ".git" / "rebase-merge").exists()
    assert _git(topo.clone, "status", "--porcelain") == ""


def test_ticker_syncs_target_even_when_run_checkout_is_detached(tmp_path: Path) -> None:
    topo = _build(tmp_path)
    remote_sha = topo.push_remote_advance()
    _git(topo.feature, "checkout", "-q", "--detach")

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == remote_sha


def test_ticker_leaves_the_agent_checkout_alone_mid_phase_but_syncs_main(
    tmp_path: Path,
) -> None:
    topo = _build(tmp_path, feature_commit=False)
    before = topo.ref(_FEATURE)
    remote_sha = topo.push_remote_advance()
    phases = AgentPhaseActivity()

    with phases.agent_phase(topo.feature):
        _run_ticks(_config(), topo.feature, agent_phases=phases)

        assert topo.ref(_TARGET) == remote_sha
        assert (topo.clone / "remote.txt").exists()
        assert topo.ref(_FEATURE) == before
        assert _git(topo.feature, "rev-parse", "HEAD") == before
        assert not (topo.feature / "remote.txt").exists()

    _run_ticks(_config(), topo.feature, agent_phases=phases)

    assert topo.ref(_FEATURE) == remote_sha
    assert (topo.feature / "remote.txt").exists()


def test_trunk_agent_phase_defers_main_but_still_fetches(tmp_path: Path) -> None:
    topo = _build(tmp_path, feature_commit=False)
    before = topo.ref(_TARGET)
    remote_sha = topo.push_remote_advance()
    phases = AgentPhaseActivity()

    with phases.agent_phase(topo.clone):
        _run_ticks(_config(), topo.clone, agent_phases=phases)

        assert _git(topo.clone, "rev-parse", f"refs/remotes/origin/{_TARGET}") == remote_sha
        assert topo.ref(_TARGET) == before
        assert not (topo.clone / "remote.txt").exists()

    _run_ticks(_config(), topo.clone, agent_phases=phases)

    assert topo.ref(_TARGET) == remote_sha
    assert (topo.clone / "remote.txt").exists()


def test_startup_recovery_fast_forwards_main_and_reports_no_failure(tmp_path: Path) -> None:
    topo = _build(tmp_path)
    remote_sha = topo.push_remote_advance()

    outcome = recover_incomplete_integration(WorkspaceScope(topo.feature), config=_config())

    assert topo.ref(_TARGET) == remote_sha
    assert outcome is None or outcome.last_remote_sync != RemoteSyncStatus.PULL_FAILED


def test_startup_recovery_fast_forwards_main_inside_ticker_cadence(tmp_path: Path) -> None:
    topo = _build(tmp_path)
    topo.push_remote_advance()
    _run_ticks(_config(), topo.feature)
    second = topo.push_remote_advance("remote2.txt")

    recover_incomplete_integration(WorkspaceScope(topo.feature), config=_config())

    assert topo.ref(_TARGET) == second


def test_remote_disabled_never_moves_local_refs_from_origin(tmp_path: Path) -> None:
    topo = _build(tmp_path)
    origin_before = topo.push_remote_advance()
    config = _config(remote_enabled=False)

    _post_commit(config, topo.feature)
    _run_ticks(config, topo.feature)
    recover_incomplete_integration(WorkspaceScope(topo.feature), config=config)

    main = topo.ref(_TARGET)
    assert _git(topo.clone, "log", "-1", "--format=%s", main) == "feature work"
    assert "remote advance" not in _git(topo.clone, "log", "--format=%s", main)
    assert topo.origin_tip() == origin_before
