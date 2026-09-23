"""Real-git proof of the background catch-up fast-forward.

The catch-up worker's whole value claim is "a checkout with no commits
of its own silently rides the moving target for free"; the unit tests
in ``tests/test_auto_integrate_catchup.py`` prove the decision gates
against fakes, and THIS file proves the real-git effect: a behind-and-clean
checkout lands exactly on the target tip.

Only the landing test marked ``subprocess_e2e`` drives real git. The worker
cadence uses injected deterministic observations so the default suite proves
that contract without rebuilding repositories.

The ``_run`` / ``_commit`` / ``_init_repo`` helpers are duplicated here
to keep this file standalone, matching the convention documented at
tests/test_auto_integrate_race.py:11-15.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_catchup as catchup

_TARGET = "main"
_FEATURE = "feature"


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _init_repo(path: Path) -> None:
    """Seed a repository whose default branch is named ``main``."""
    path.mkdir(parents=True, exist_ok=True)
    assert _run(path, "init").returncode == 0
    assert _run(path, "config", "user.email", "test@example.com").returncode == 0
    assert _run(path, "config", "user.name", "Test User").returncode == 0
    _commit(path, "seed.txt", "seed\n", "seed")
    assert _run(path, "branch", "-M", _TARGET).returncode == 0


def _head_sha(repo_root: Path) -> str:
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(*, enabled: bool = True, remote_enabled: bool = False) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": enabled,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote_enabled": remote_enabled,
                "auto_integrate_remote_interval_seconds": 0.0,
            }
        }
    )


def _repo_with_feature_behind_main(tmp_path: Path) -> tuple[Path, str]:
    """Checkout on ``feature`` at the seed commit while ``main`` moved on.

    Returns ``(repo_root, advanced_main_sha)``.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    assert _run(repo, "branch", _FEATURE).returncode == 0
    advanced = _commit(repo, "mainline.txt", "landed\n", "mainline advance")
    assert _run(repo, "checkout", _FEATURE).returncode == 0
    return repo, advanced


def _repo_with_remote_ahead(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    bare = tmp_path / "origin.git"
    writer = tmp_path / "writer"
    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(tmp_path, "clone", str(bare), str(writer)).returncode == 0
    assert _run(writer, "config", "user.email", "test@example.com").returncode == 0
    assert _run(writer, "config", "user.name", "Test User").returncode == 0
    remote_sha = _commit(writer, "remote.txt", "remote\n", "remote advance")
    assert _run(writer, "push", "origin", _TARGET).returncode == 0
    return repo, bare, remote_sha


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_behind_and_clean_checkout_lands_on_target_tip(tmp_path: Path) -> None:
    repo, advanced = _repo_with_feature_behind_main(tmp_path)
    outcome = catchup.attempt_catchup_fast_forward(_build_config(), repo)
    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert _head_sha(repo) == advanced
    # The working tree advanced with the ref: the landed file is present.
    assert (repo / "mainline.txt").read_text(encoding="utf-8") == "landed\n"
    # And the branch itself moved, not a detached HEAD.
    branch = _run(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
    assert branch == _FEATURE


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_ahead_advances_local_target_then_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    bare = tmp_path / "origin.git"
    writer = tmp_path / "writer"
    _init_repo(repo)
    assert _run(repo, "branch", _FEATURE).returncode == 0
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(tmp_path, "clone", str(bare), str(writer)).returncode == 0
    assert _run(writer, "config", "user.email", "test@example.com").returncode == 0
    assert _run(writer, "config", "user.name", "Test User").returncode == 0
    remote_sha = _commit(writer, "remote.txt", "remote\n", "remote advance")
    assert _run(writer, "push", "origin", _TARGET).returncode == 0
    assert _run(repo, "checkout", _FEATURE).returncode == 0

    outcome = catchup.attempt_catchup_fast_forward(
        _build_config(remote_enabled=True), repo
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert _run(repo, "rev-parse", f"refs/heads/{_TARGET}").stdout.strip() == remote_sha
    assert _head_sha(repo) == remote_sha
    assert (repo / "remote.txt").read_text(encoding="utf-8") == "remote\n"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_ahead_advances_target_with_head_on_target(tmp_path: Path) -> None:
    repo, _bare, remote_sha = _repo_with_remote_ahead(tmp_path)

    outcome = catchup.attempt_catchup_fast_forward(
        _build_config(remote_enabled=True), repo
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert _head_sha(repo) == remote_sha
    assert (repo / "remote.txt").read_text(encoding="utf-8") == "remote\n"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_ahead_advances_clean_linked_target_owner_by_strict_ff(tmp_path: Path) -> None:
    repo, _bare, remote_sha = _repo_with_remote_ahead(tmp_path)
    feature_owner = tmp_path / "feature-owner"
    assert _run(repo, "branch", _FEATURE).returncode == 0
    assert _run(repo, "worktree", "add", str(feature_owner), _FEATURE).returncode == 0

    outcome = catchup.attempt_catchup_fast_forward(
        _build_config(remote_enabled=True), feature_owner
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert _head_sha(repo) == remote_sha
    assert _head_sha(feature_owner) == remote_sha
    assert (repo / "remote.txt").read_text(encoding="utf-8") == "remote\n"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_ahead_leaves_dirty_linked_target_owner_unchanged(tmp_path: Path) -> None:
    repo, _bare, _remote_sha = _repo_with_remote_ahead(tmp_path)
    feature_owner = tmp_path / "feature-owner"
    assert _run(repo, "branch", _FEATURE).returncode == 0
    assert _run(repo, "worktree", "add", str(feature_owner), _FEATURE).returncode == 0
    target_before = _head_sha(repo)
    feature_before = _head_sha(feature_owner)
    index_before = _run(repo, "write-tree").stdout.strip()
    (repo / "seed.txt").write_text("dirty\n", encoding="utf-8")

    outcome = catchup.attempt_catchup_fast_forward(
        _build_config(remote_enabled=True), feature_owner
    )

    assert outcome == catchup.CATCHUP_REFUSED
    assert _head_sha(repo) == target_before
    assert _head_sha(feature_owner) == feature_before
    assert _run(repo, "write-tree").stdout.strip() == index_before
    assert (repo / "seed.txt").read_text(encoding="utf-8") == "dirty\n"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_os_lock_excludes_linked_worktree_remote_transaction(tmp_path: Path) -> None:
    repo, _bare, _remote_sha = _repo_with_remote_ahead(tmp_path)
    feature_owner = tmp_path / "feature-owner"
    assert _run(repo, "branch", _FEATURE).returncode == 0
    assert _run(repo, "worktree", "add", str(feature_owner), _FEATURE).returncode == 0
    holder_code = """
import sys
from pathlib import Path
from ralph.pipeline.auto_integrate_catchup_coordination import remote_sync_transaction
with remote_sync_transaction(Path(sys.argv[1]), 'origin', 'main') as lease:
    print('LOCKED' if lease is not None else 'FAILED', flush=True)
    sys.stdin.readline()
"""
    holder = subprocess.Popen(
        (sys.executable, "-c", holder_code, str(repo)),
        cwd=str(repo),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1]),
        },
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"
        target_before = _head_sha(repo)
        feature_before = _head_sha(feature_owner)

        outcome = catchup.attempt_catchup_fast_forward(
            _build_config(remote_enabled=True), feature_owner
        )

        assert outcome == catchup.CATCHUP_REMOTE_SKIPPED
        assert _head_sha(repo) == target_before
        assert _head_sha(feature_owner) == feature_before
        tracking = _run(feature_owner, "show-ref", "--verify", "refs/remotes/origin/main")
        assert tracking.returncode != 0
    finally:
        stdout, stderr = holder.communicate(input="release\n", timeout=5)
        assert holder.returncode == 0, stdout + stderr


def test_checkout_on_target_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catchup, "_current_branch_name", lambda _root: _TARGET)
    monkeypatch.setattr(catchup, "resolve_integration_target", lambda _config, _root: _TARGET)
    outcome = catchup.attempt_catchup_fast_forward(_build_config(), Path("/workspace"))
    assert outcome == catchup.CATCHUP_ON_TARGET


def test_disabled_config_never_touches_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_git(_root: Path) -> str:
        raise AssertionError("disabled catch-up touched git")

    monkeypatch.setattr(catchup, "_current_branch_name", _unexpected_git)
    outcome = catchup.attempt_catchup_fast_forward(_build_config(enabled=False), Path("/workspace"))
    assert outcome == catchup.CATCHUP_DISABLED


def test_branch_names_shadowing_list_methods_resolve_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Branch names like ``append``/``count`` must not fool the ref reads.

    GitPython's ``IterableList[name]`` falls back to ``getattr``, so a
    lookup for a branch named after a ``list`` method returns the bound
    method: a missing branch reads as present, and a REAL branch named
    ``append`` reads as unreadable (adversarial-review reproduction).
    The catch-up's exact-name lookup must get both directions right.
    """

    class _Head:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Heads(list[_Head]):
        append = list.append

    repo = type("_Repo", (), {"heads": _Heads([_Head("append")])})()
    assert catchup._local_head(repo, "append").name == "append"
    for phantom in ("count", "index", "sort", "copy", "pop"):
        assert catchup._local_head(repo, phantom) is None


def test_worker_runner_uses_injected_wait_cadence() -> None:
    """The worker runner ticks on cadence without a real thread or clock."""
    waits = iter((False, False, True))
    intervals: list[float] = []
    outcomes: list[str] = []

    def _wait(interval: float) -> bool:
        intervals.append(interval)
        return next(waits)

    def _observing_tick() -> str:
        outcomes.append(catchup.CATCHUP_FAST_FORWARDED)
        return outcomes[-1]

    worker = catchup.AutoIntegrateCatchupWorker(
        _build_config(),
        Path("/workspace"),
        interval_seconds=3.0,
        tick=_observing_tick,
        wait=_wait,
    )
    worker.run()

    assert intervals == [3.0, 3.0, 3.0]
    assert outcomes == [
        catchup.CATCHUP_FAST_FORWARDED,
        catchup.CATCHUP_FAST_FORWARDED,
    ]
