"""Real-Git contract: with remote sync enabled, local branches follow origin.

The contract
============

When ``general.auto_integrate_remote_enabled`` is true and the configured
remote (``general.auto_integrate_remote``, default ``origin``) exists, every
automatic integration entry point keeps the local target branch in step with
``<remote>/<target>``:

* the **post-commit integration** (``auto_integrate_after_commit``, the seam
  the runner calls after every successful commit),
* the **startup recovery seam** (``recover_incomplete_integration``), and
* the **periodic catch-up ticker** (``AutoIntegrateCatchupWorker``, the
  background thread ``start_catchup_worker_if_enabled`` starts for every
  run)

each fetch the remote target, fast-forward the local target to it when that
is a TRUE fast-forward, and then integrate the feature branch onto the moved
target (rebase-and-land at the post-commit seam; fast-forward of a checkout
with no commits of its own at the ticker). A target that is checked out in a
worktree is advanced THROUGH that worktree (``merge --ff-only``) so its index
and files move with the ref.

Unsafe states fail closed and are reported, never forced:

* a dirty checkout that owns the target is left exactly as it was (ref,
  index and files); the ticker retries once it is clean;
* a target that diverged from the remote is never force-moved; a conflicting
  reconciliation aborts cleanly and leaves both the local and the remote ref
  where they were.

While an agent phase runs in a checkout, the ticker never moves THAT
checkout (nor the target, when the agent works on it directly), but it keeps
fetching and keeps advancing a target checked out elsewhere; the first tick
after the phase lands what it deferred.

With remote sync DISABLED, auto-integration stays local-only: no remote state
ever moves a local ref.

The bug this guards against
===========================

Reported 2026-10-30: "with remote sync enabled, the automatic rebase does not
fast-forward the branches to match origin", and the periodic task did not do
it either. Root causes (see ``docs/agents/remote-sync.md``):

1. ``auto_integrate_after_commit`` recorded the ``on target branch`` and
   ``no commits beyond target`` skips BEFORE the remote was consulted, so a
   run on the target branch (Ralph's trunk-based layout) or a feature branch
   sitting at the local target never pulled or published at all.
2. The ticker coupled "may I fetch" with "may I advance": a tick inside the
   five-minute fetch cadence never applied a remote-tracking ref that was
   already known to be ahead, and a refused advance (dirty target owner) was
   not retried until the next fetch window.
3. The publication reconciler (startup seam, ticker post-tick publication)
   delegated "remote is ahead" to the whole catch-up tick. That tick is
   throttled, re-acquires the mutation lease the ticker already holds (so it
   always refused itself), and reports the CHECKOUT's catch-up instead of the
   target's: the target advance was skipped or reported as ``pull failed``.
4. A detached checkout made the ticker return before the target sync ran.

Every remote here is a local bare repository: no test reaches the network,
and no coding agent is ever started.

Scenario setup
==============

Every scenario needs the same topology -- a bare origin, a clone holding
``main``, a linked worktree on ``feature`` and a writer clone -- and the
scenarios differ only in the remote's name and in whether the feature branch
owns a commit. The three topologies are therefore built ONCE per session and
copied per test: a copy is a filesystem copy plus a rewrite of the absolute
paths git records for the remote URL and for the linked worktree, so the
objects, refs and index come with it. The commits and the repository
metadata are written by GitPython, which needs no process spawn at all; the
copies themselves cost no git invocation either. The remote advances are
published the same way: the writer clone makes the commit and the objects it
introduced are copied into the remote, which is the state a ``git push``
leaves the remote in.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
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

# Each scenario builds a bare origin and three checkouts with real git; on a
# loaded xdist host that takes several seconds, so the per-test budget is
# raised for these real-Git proofs only.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_TARGET = "main"
_FEATURE = "feature"
#: Name of the linked worktree the agent runs in (Ralph's agent layout).
_WORKTREE = "wt-001-feature"

#: The scenario topologies, built once per session by the
#: ``_scenario_templates`` fixture: variant name -> template root. The
#: container is empty only before the fixture has run (the fixture is
#: autouse, so every test sees a populated template).
_TEMPLATES: dict[str, Path] = {}

#: The topology variants the scenarios need: variant name -> (remote name,
#: whether the feature branch owns a commit).
_VARIANTS: dict[str, tuple[str, bool]] = {
    "origin-plain": ("origin", False),
    "origin-feature": ("origin", True),
    "tacos-feature": ("tacos", True),
}


def _git(cwd: Path, *args: str) -> str:
    """Run ``git`` in ``cwd`` and return stripped stdout; fail loudly on error."""
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
    """Commit a file and return the new tip's SHA.

    GitPython writes the blob, the tree and the commit object itself, so a
    commit costs no process spawn; ``git add`` + ``git commit`` answered the
    same question for a spawn each.
    """
    (repo / filename).write_text(content, encoding="utf-8")
    git_repo = Repo(repo)
    try:
        git_repo.index.add([filename])
        return git_repo.index.commit(message).hexsha
    finally:
        git_repo.close()


def _identify(repo: Path) -> None:
    """Give the repository a committer identity, without a process spawn."""
    git_repo = Repo(repo)
    try:
        writer = git_repo.config_writer()
        try:
            writer.set_value("user", "email", "test@example.com")
            writer.set_value("user", "name", "Test User")
        finally:
            writer.release()
    finally:
        git_repo.close()


def _publish_commit(writer: Path, bare: Path, filename: str, content: str, message: str) -> str:
    """Commit a file in the writer clone and publish it to the bare remote.

    The commit is a real git commit, made by GitPython in the writer; the
    publication copies the objects it introduced into the remote's object
    database and moves the remote's ref. That is what ``git push`` does to
    the remote's state, without the transport's process spawns -- a push
    costs a receive-pack per advance, and every scenario pays for one.
    """
    (writer / filename).write_text(content, encoding="utf-8")
    git_repo = Repo(writer)
    try:
        git_repo.index.add([filename])
        commit = git_repo.index.commit(message)
        _copy_objects(git_repo, Repo(bare), commit)
    finally:
        git_repo.close()
    _set_ref(bare, f"refs/heads/{_TARGET}", commit.hexsha)
    return commit.hexsha


def _copy_objects(source: Repo, target: Repo, commit: Commit) -> None:
    """Copy the objects a commit introduced into the target's object database.

    Git objects are immutable, so the loose files are copied verbatim; the
    objects the target already holds are left alone.
    """
    source_objects = Path(source.git_dir) / "objects"
    target_objects = Path(target.git_dir) / "objects"
    for sha in [commit.hexsha, *_tree_shas(commit.tree)]:
        name = f"{sha[:2]}/{sha[2:]}"
        destination = target_objects / name
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_objects / name, destination)


def _tree_shas(tree: Tree) -> list[str]:
    """The SHAs of a tree and of everything below it."""
    shas = [tree.hexsha]
    for entry in tree:
        if isinstance(entry, Tree):
            shas.extend(_tree_shas(entry))
        else:
            shas.append(entry.hexsha)
    return shas


def _set_ref(git_dir: Path, ref: str, sha: str) -> None:
    """Write a loose ref, exactly as git does when a ref is updated."""
    path = git_dir / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{sha}\n", encoding="utf-8")


def _ref_sha(git_dir: Path, name: str) -> str:
    """Read a branch tip from the repository's own ref files.

    ``git rev-parse`` answers the same question, but it costs a process spawn
    per call; the loose ref (or its packed form) is the answer itself.
    """
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
    """A clone with local ``main`` + ``feature`` and a second writer clone.

    ``clone`` is the primary checkout (``main`` checked out there);
    ``feature`` is the checkout Ralph runs in (a linked worktree on
    ``feature``); ``writer`` makes the remote advance that ``bare`` publishes.
    """

    bare: Path
    clone: Path
    feature: Path
    writer: Path
    remote: str

    def ref(self, name: str) -> str:
        """The local tip of ``name``."""
        return _ref_sha(self.clone / ".git", name)

    def origin_tip(self) -> str:
        """The tip of ``<remote>/main``."""
        return _ref_sha(self.bare, _TARGET)

    def push_remote_advance(self, filename: str = "remote.txt") -> str:
        """Advance ``<remote>/main`` by one commit, made in the writer clone."""
        if _ref_sha(self.writer / ".git", _TARGET) != _ref_sha(self.bare, _TARGET):
            # The writer trails the remote (the publication reconciler moved
            # it); catch up before committing on top of it.
            _git(self.writer, "pull", "-q", "--ff-only", self.remote, _TARGET)
        return _publish_commit(
            self.writer, self.bare, filename, f"{filename}\n", f"remote advance {filename}"
        )


def _ignore_inert_git_samples(directory: str, names: list[str]) -> set[str]:
    """Skip git's inert ``*.sample`` hooks when copying a scenario.

    Nothing ever runs a ``*.sample`` file, so copying the fourteen of them
    into every test's repository only costs filesystem time.
    """
    del names
    name = Path(directory).name
    return {"hooks"} if name == ".git" or name.endswith(".git") else set()


def _rebase_paths(source: Path, target: Path) -> None:
    """Point the copied repository at the copy's own location.

    ``git`` records absolute paths for the remote URL and for a linked
    worktree's administrative files; a copy carries the template's paths
    until they are rewritten.
    """
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
    """Build one scenario topology under ``root``.

    ``root`` holds a bare origin, a clone with ``main``, a linked worktree
    on ``feature`` and a writer clone -- the copy source for the tests.
    """
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


@pytest.fixture(scope="session", autouse=True)
def _scenario_templates(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Create the scenario topologies once per session.

    Building a topology costs a real ``git clone``, a real ``git worktree
    add`` and a real ``git commit``, and none of that work varies between
    tests. The templates are built once; each test copies the directory it
    needs, so every scenario still starts from its own pristine,
    independently mutable repositories.
    """
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
    """Copy the prepared topology into this test's own directory.

    The copy carries the objects, refs and index of the template, so the
    scenario starts from the same pristine state a freshly built topology
    would give -- without paying for the git invocations that build one.
    """
    variant = f"{remote}-{'feature' if feature_commit else 'plain'}"
    template = _TEMPLATES[variant]
    shutil.copytree(template, tmp_path, dirs_exist_ok=True, ignore=_ignore_inert_git_samples)
    _rebase_paths(template, tmp_path)
    return _Topology(
        bare=tmp_path / "origin.git",
        clone=tmp_path / "clone",
        feature=tmp_path / _WORKTREE,
        writer=tmp_path / "writer",
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
    """Run ``ticks`` real catch-up ticks through the worker's public loop."""
    stops = iter([False] * ticks + [True])
    worker = AutoIntegrateCatchupWorker(
        config, root, wait=lambda _interval: next(stops), agent_phases=agent_phases
    )
    worker.run()


# ---------------------------------------------------------------------------
# Post-commit integration
# ---------------------------------------------------------------------------


def test_post_commit_from_feature_worktree_pulls_origin_then_lands_feature(
    tmp_path: Path,
) -> None:
    """Feature worktree, origin ahead: main follows origin, feature lands on top.

    Expected git state: local ``main`` contains the origin commit AND the
    feature commit, ``origin/main`` equals local ``main`` (published), and the
    primary checkout that owns ``main`` is clean with the files on disk.
    This is the everyday Ralph agent layout and must never regress.
    """
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
    """Run checkout ON main (trunk layout), origin ahead: main fast-forwards.

    Expected git state: local ``main`` equals the origin tip and the
    checkout's files include the remote commit. Before the fix the seam
    recorded ``on target branch`` without ever fetching, so a trunk-based run
    never caught up with origin after a commit (root cause 1).
    """
    topo = _build(tmp_path, feature_commit=False)
    remote_sha = topo.push_remote_advance()

    _post_commit(_config(), topo.clone)

    assert topo.ref(_TARGET) == remote_sha
    assert (topo.clone / "remote.txt").read_text(encoding="utf-8") == "remote.txt\n"
    assert _git(topo.clone, "status", "--porcelain") == ""


def test_post_commit_on_target_branch_publishes_and_reconciles_local_commits(
    tmp_path: Path,
) -> None:
    """Run checkout ON main with a new local commit while origin also moved.

    Expected git state: local ``main`` contains BOTH the origin commit and the
    local commit (nothing dropped, nothing forced), and origin equals local
    ``main``. Before the fix the on-target skip neither pulled nor pushed, so
    the local commit stayed unpublished and main stayed behind (root cause 1).
    """
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
    """Feature branch with no commits of its own, origin ahead.

    Expected git state: after the post-commit seam ``main`` equals the origin
    tip; the feature (fully contained in ``main``, so already "landed") is
    then fast-forwarded onto it by the next catch-up tick. Before the fix the
    seam recorded ``no commits beyond target`` from the STALE local pointer
    and returned before fetching, so ``main`` never moved (root cause 1).
    """
    topo = _build(tmp_path, feature_commit=False)
    remote_sha = topo.push_remote_advance()

    _post_commit(_config(), topo.feature)

    assert topo.ref(_TARGET) == remote_sha

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_FEATURE) == remote_sha
    assert (topo.feature / "remote.txt").exists()


def test_post_commit_on_dirty_target_checkout_fails_closed(tmp_path: Path) -> None:
    """Run checkout ON main with an uncommitted tracked edit, origin ahead.

    Expected git state: ``main`` is unchanged and the user's edit is intact
    (no reset, no stash). A dirty checkout that owns the target is an unsafe
    state; the seam must defer rather than move the ref under the edit.
    """
    topo = _build(tmp_path, feature_commit=False)
    before = topo.ref(_TARGET)
    topo.push_remote_advance()
    (topo.clone / "seed.txt").write_text("uncommitted edit\n", encoding="utf-8")

    _post_commit(_config(), topo.clone)

    assert topo.ref(_TARGET) == before
    assert (topo.clone / "seed.txt").read_text(encoding="utf-8") == "uncommitted edit\n"
    assert _git(topo.clone, "stash", "list") == ""


def test_post_commit_uses_the_configured_non_default_remote(tmp_path: Path) -> None:
    """The remote is named ``tacos``; there is no ``origin`` at all.

    Expected git state: ``main`` contains the ``tacos/main`` commit and the
    feature work, and ``tacos/main`` equals local ``main``. Guards against any
    path that silently assumes the remote is called ``origin``.
    """
    topo = _build(tmp_path, remote="tacos")
    remote_sha = topo.push_remote_advance()

    _post_commit(_config(remote="tacos"), topo.feature)

    main = topo.ref(_TARGET)
    assert _is_ancestor(topo.clone, remote_sha, main)
    assert topo.origin_tip() == main


# ---------------------------------------------------------------------------
# Periodic catch-up ticker
# ---------------------------------------------------------------------------


def test_start_catchup_worker_runs_a_ticker_when_remote_sync_is_enabled(
    tmp_path: Path,
) -> None:
    """The run's ticker exists and is running when remote sync is enabled.

    Expected state: a live worker thread is returned. If the ticker were not
    started, nothing would ever pull between commits.
    """
    topo = _build(tmp_path)
    worker = start_catchup_worker_if_enabled(_config(), topo.feature, interval_seconds=3600.0)
    try:
        assert worker is not None
        assert worker.is_running
    finally:
        if worker is not None:
            worker.stop()


def test_ticker_fast_forwards_main_checked_out_in_another_worktree(tmp_path: Path) -> None:
    """Ticker in the feature worktree; ``main`` is checked out in the clone.

    Expected git state: ``main`` equals the origin tip and the clone that owns
    ``main`` advanced its index and files with it (``remote.txt`` on disk,
    clean status). A bare ``update-ref`` would have desynchronised it.
    """
    topo = _build(tmp_path)
    remote_sha = topo.push_remote_advance()

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == remote_sha
    assert (topo.clone / "remote.txt").exists()
    assert _git(topo.clone, "status", "--porcelain") == ""


def test_ticker_fast_forwards_target_when_run_checkout_is_on_target(tmp_path: Path) -> None:
    """Ticker in a checkout that is itself on ``main`` (trunk layout).

    Expected git state: ``main`` equals the origin tip and the files moved.
    """
    topo = _build(tmp_path, feature_commit=False)
    remote_sha = topo.push_remote_advance()

    _run_ticks(_config(), topo.clone)

    assert topo.ref(_TARGET) == remote_sha
    assert (topo.clone / "remote.txt").exists()


def test_ticker_applies_a_remote_advance_inside_the_fetch_cadence(tmp_path: Path) -> None:
    """Two ticks seconds apart; origin moves between them.

    Expected git state: after the second tick ``main`` equals the NEW origin
    tip. The ticker's own fetch runs at most every five minutes, but the
    remote-tracking ref is refreshed in between (post-tick publication, other
    seams); before the fix a throttled tick never applied it and the
    publication's nested catch-up refused itself on the ticker's own lease
    (root causes 2 and 3).
    """
    topo = _build(tmp_path)
    topo.push_remote_advance()
    _run_ticks(_config(), topo.feature)
    second = topo.push_remote_advance("remote2.txt")

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == second
    assert (topo.clone / "remote2.txt").exists()


def test_ticker_refuses_dirty_target_owner_then_retries_once_clean(tmp_path: Path) -> None:
    """The clone owning ``main`` has an uncommitted edit, then is cleaned.

    Expected git state: the first tick leaves ``main``, the clone's index and
    the edit untouched (fail closed); after the edit is reverted the next tick
    -- inside the same fetch cadence -- fast-forwards ``main`` to origin.
    Before the fix the refused advance waited out the whole cadence (root
    cause 2).
    """
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
    """Local ``main`` has a commit that conflicts with the origin advance.

    Expected git state: local ``main`` still names the local commit, origin is
    unchanged, and no rebase is left in progress. Divergence is never resolved
    by a forced move; the conflicting reconciliation aborts cleanly.
    """
    topo = _build(tmp_path)
    origin_before = topo.push_remote_advance()
    local_sha = _commit(topo.clone, "remote.txt", "local version\n", "local conflicting")

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == local_sha
    assert topo.origin_tip() == origin_before
    assert not (topo.clone / ".git" / "rebase-merge").exists()
    assert _git(topo.clone, "status", "--porcelain") == ""


def test_ticker_syncs_target_even_when_run_checkout_is_detached(tmp_path: Path) -> None:
    """The run checkout sits on a detached HEAD; ``main`` lives in the clone.

    Expected git state: ``main`` equals the origin tip. The target sync does
    not depend on which branch the run checkout is on; before the fix the tick
    returned ``not-on-branch`` before syncing (root cause 4).
    """
    topo = _build(tmp_path)
    remote_sha = topo.push_remote_advance()
    _git(topo.feature, "checkout", "-q", "--detach")

    _run_ticks(_config(), topo.feature)

    assert topo.ref(_TARGET) == remote_sha


def test_ticker_leaves_the_agent_checkout_alone_mid_phase_but_syncs_main(
    tmp_path: Path,
) -> None:
    """An agent phase runs in the clean feature worktree; origin moves.

    Scenario: the feature worktree has no commits of its own, so once
    ``main`` follows origin it is clean and strictly behind -- exactly what
    the ticker fast-forwards. An agent is working in it (planning or reading,
    so nothing is dirty).

    Expected git state: during the phase ``main`` (checked out in the clone)
    equals the origin tip with its files on disk, while the feature worktree
    keeps its old HEAD and has no ``remote.txt``. After the phase ends, the
    next tick fast-forwards the feature onto the origin tip.

    Why: before the fix only "the worktree is dirty" protected a running
    agent, so the files of a clean checkout changed underneath it mid-phase.
    Remote sync must not stop for the hours an agent phase can last, so the
    target keeps following origin meanwhile.
    """
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
    """An agent phase runs in the clone, which is ON ``main`` (trunk layout).

    Expected git state: during the phase ``origin/main`` (the tracking ref)
    already names the new origin tip -- the fetch kept its cadence -- but
    local ``main`` and the clone's files are unchanged. After the phase, the
    next tick fast-forwards ``main`` and its files.

    Why: in the trunk layout the target's checkout IS the agent's checkout;
    ``merge --ff-only`` there would rewrite files under the agent.
    """
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


# ---------------------------------------------------------------------------
# Startup recovery
# ---------------------------------------------------------------------------


def test_startup_recovery_fast_forwards_main_and_reports_no_failure(tmp_path: Path) -> None:
    """Startup with origin ahead and a feature branch with its own commit.

    Expected git state: ``main`` equals the origin tip, and the recovery
    verdict does not claim ``pull failed``. Before the fix the target moved
    but the seam reported a failed pull with a false ``unreachable`` refresh,
    because it judged the feature checkout's catch-up (root cause 3).
    """
    topo = _build(tmp_path)
    remote_sha = topo.push_remote_advance()

    outcome = recover_incomplete_integration(WorkspaceScope(topo.feature), config=_config())

    assert topo.ref(_TARGET) == remote_sha
    assert outcome is None or outcome.last_remote_sync != RemoteSyncStatus.PULL_FAILED


def test_startup_recovery_fast_forwards_main_inside_ticker_cadence(tmp_path: Path) -> None:
    """A ticker fetched seconds ago; origin moved; then a run starts.

    Expected git state: ``main`` equals the new origin tip after startup.
    Before the fix the startup seam routed through the throttled tick and did
    not advance at all inside the five-minute cadence (root cause 3).
    """
    topo = _build(tmp_path)
    topo.push_remote_advance()
    _run_ticks(_config(), topo.feature)
    second = topo.push_remote_advance("remote2.txt")

    recover_incomplete_integration(WorkspaceScope(topo.feature), config=_config())

    assert topo.ref(_TARGET) == second


# ---------------------------------------------------------------------------
# Remote sync disabled: local-only
# ---------------------------------------------------------------------------


def test_remote_disabled_never_moves_local_refs_from_origin(tmp_path: Path) -> None:
    """Remote sync disabled; origin ahead; post-commit, ticker and startup run.

    Expected git state: local ``main`` never contains the origin commit and
    origin is unchanged. The feature still integrates onto LOCAL ``main``.
    This is the deliberate local-only contract for remote-disabled configs.
    """
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
