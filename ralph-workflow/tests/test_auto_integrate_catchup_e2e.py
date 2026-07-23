"""Real-git proof of the background catch-up fast-forward.

The catch-up worker's whole value claim is "a checkout with no commits
of its own silently rides the moving target for free"; the unit tests
in ``tests/test_auto_integrate_catchup.py`` prove the gate ordering
against fakes, and THIS file proves the git effects: a behind-and-clean
checkout lands exactly on the target tip, while divergence, dirt, and
being on the target itself all leave the repository byte-identical.

File-level markers: ``subprocess_e2e`` keeps this file out of the
budget-tracked ``make test`` step (every test drives real git);
``timeout_seconds(20)`` sizes the budget for repository fixtures. The
file runs under the bounded ``make test-auto-integrate-e2e`` target
that ``make verify`` invokes.

The ``_run`` / ``_commit`` / ``_init_repo`` helpers are duplicated here
to keep this file standalone, matching the convention documented at
tests/test_auto_integrate_race.py:11-15.
"""

from __future__ import annotations

import subprocess
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_catchup as catchup

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

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


def _build_config(*, enabled: bool = True) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": enabled,
                "auto_integrate_target": _TARGET,
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


def test_diverged_checkout_is_left_untouched(tmp_path: Path) -> None:
    repo, _advanced = _repo_with_feature_behind_main(tmp_path)
    own = _commit(repo, "feature.txt", "own work\n", "feature work")
    outcome = catchup.attempt_catchup_fast_forward(_build_config(), repo)
    assert outcome == catchup.CATCHUP_DIVERGED
    assert _head_sha(repo) == own


def test_dirty_worktree_defers_without_mutation(tmp_path: Path) -> None:
    repo, _advanced = _repo_with_feature_behind_main(tmp_path)
    before = _head_sha(repo)
    (repo / "seed.txt").write_text("uncommitted edit\n", encoding="utf-8")
    outcome = catchup.attempt_catchup_fast_forward(_build_config(), repo)
    assert outcome == catchup.CATCHUP_DIRTY
    assert _head_sha(repo) == before
    assert (repo / "seed.txt").read_text(encoding="utf-8") == "uncommitted edit\n"


def test_checkout_on_target_is_skipped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    outcome = catchup.attempt_catchup_fast_forward(_build_config(), repo)
    assert outcome == catchup.CATCHUP_ON_TARGET


def test_disabled_config_never_touches_git(tmp_path: Path) -> None:
    repo, advanced = _repo_with_feature_behind_main(tmp_path)
    outcome = catchup.attempt_catchup_fast_forward(
        _build_config(enabled=False), repo
    )
    assert outcome == catchup.CATCHUP_DISABLED
    assert _head_sha(repo) != advanced


def test_quiet_resolver_matches_seam_resolver(tmp_path: Path) -> None:
    """The in-process target resolver answers exactly like the seams' resolver.

    The catch-up moving the checkout toward a DIFFERENT branch than the
    seams integrate onto would be actively harmful, so the quiet
    GitPython mirror is pinned byte-identical to
    :func:`ralph.pipeline.auto_integrate.resolve_integration_target`
    across the precedence rungs: configured-and-existing, configured-
    but-missing, and the main/master auto-detection fallbacks.
    """
    from ralph.pipeline.auto_integrate import (
        resolve_integration_target as seam_resolve,
    )

    repo = tmp_path / "repo"
    _init_repo(repo)
    assert _run(repo, "branch", _FEATURE).returncode == 0

    configured = _build_config()
    missing = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_target": "no-such-branch"}}
    )
    autodetect = UnifiedConfig.model_validate({"general": {}})
    for config in (configured, missing, autodetect):
        assert catchup.resolve_integration_target(config, repo) == seam_resolve(
            config, repo
        )

    master_repo = tmp_path / "master-repo"
    _init_repo(master_repo)
    assert _run(master_repo, "branch", "-M", "master").returncode == 0
    assert catchup.resolve_integration_target(autodetect, master_repo) == seam_resolve(
        autodetect, master_repo
    )


def test_worker_thread_lands_the_catchup_end_to_end(tmp_path: Path) -> None:
    """The daemon worker itself performs the fast-forward on its cadence."""
    repo, advanced = _repo_with_feature_behind_main(tmp_path)
    landed = threading.Event()
    config = _build_config()

    def _observing_tick() -> str:
        outcome = catchup.attempt_catchup_fast_forward(config, repo)
        if outcome == catchup.CATCHUP_FAST_FORWARDED:
            landed.set()
        return outcome

    worker = catchup.AutoIntegrateCatchupWorker(
        config, repo, interval_seconds=0.05, tick=_observing_tick
    )
    worker.start()
    try:
        assert landed.wait(timeout=10.0)
    finally:
        worker.stop()
    assert _head_sha(repo) == advanced
