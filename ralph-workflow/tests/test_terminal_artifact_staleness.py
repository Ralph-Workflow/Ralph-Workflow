from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from ralph.phases.artifact_freshness import artifact_fingerprint
from ralph.pipeline.effect_executor import has_valid_terminal_artifact

PLAN = "# Plan\n"


class _KilledError(Exception):
    returncode = 143


def _ctx(root: Path) -> SimpleNamespace:
    return SimpleNamespace(workspace_scope=SimpleNamespace(root=root))


def _artifact() -> SimpleNamespace:
    return SimpleNamespace(artifact_path=".agent/artifacts/plan.md", artifact_type="plan")


def _write_plan(root: Path) -> Path:
    path = root / ".agent/artifacts/plan.md"
    path.parent.mkdir(parents=True)
    path.write_text(PLAN)
    return path


def test_stale_artifact_not_accepted_after_kill(tmp_path: Path) -> None:
    _write_plan(tmp_path)
    ctx, art = _ctx(tmp_path), _artifact()
    baseline = artifact_fingerprint(tmp_path / art.artifact_path)
    assert not has_valid_terminal_artifact(ctx, art, _KilledError(), baseline=baseline)


def test_rewritten_artifact_changes_the_fingerprint(tmp_path: Path) -> None:
    path = _write_plan(tmp_path)
    art = _artifact()
    baseline = artifact_fingerprint(tmp_path / art.artifact_path)
    path.write_text(PLAN + "revised\n")
    os.utime(path, ns=(1, 2_000_000_000_000_000_000))
    assert artifact_fingerprint(tmp_path / art.artifact_path) != baseline
