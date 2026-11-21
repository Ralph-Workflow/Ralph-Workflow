from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate_catchup import CATCHUP_DIVERGED, AutoIntegrateCatchupWorker

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(60)]

_IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@t")


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True, timeout=20
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(root: Path, name: str, content: str, message: str) -> None:
    (root / name).write_text(content, encoding="utf-8")
    _git(root, "add", name)
    _git(root, *_IDENTITY, "commit", "-q", "-m", message)


def _paused_rebase_in_a_run_worktree(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    main = tmp_path / "main"
    _git(tmp_path, "clone", "-q", str(origin), str(main))
    _commit(main, "shared.txt", "base\n", "base")
    _git(main, "push", "-q", "origin", "HEAD:main")
    feature = tmp_path / "wt-feature"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(feature))
    _commit(feature, "shared.txt", "feature\n", "feature change")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    _commit(other, "remote.txt", "remote\n", "remote-only change")
    _git(other, "push", "-q", "origin", "HEAD:main")
    _commit(main, "shared.txt", "target\n", "target change")
    _git(feature, "rebase", "main", check=False)
    assert _git(feature, "rev-parse", "--verify", "--quiet", "REBASE_HEAD", check=False)
    return feature


class _OneTick:
    def __init__(self) -> None:
        self.remaining = 1

    def wait(self, _interval: float) -> bool:
        if self.remaining <= 0:
            return True
        self.remaining -= 1
        return False


def test_a_catch_up_tick_never_aborts_the_rebase_the_run_is_resolving(tmp_path: Path) -> None:
    feature = _paused_rebase_in_a_run_worktree(tmp_path)
    stop_before = _git(feature, "rev-parse", "REBASE_HEAD")
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote": "origin",
            }
        }
    )
    ticks = _OneTick()
    worker = AutoIntegrateCatchupWorker(
        config, feature, tick=lambda: CATCHUP_DIVERGED, wait=ticks.wait
    )

    worker.run()

    assert _git(feature, "rev-parse", "--verify", "--quiet", "REBASE_HEAD", check=False) == (
        stop_before
    )
    assert "abort" not in _git(feature, "reflog", "-1", "--format=%gs")
