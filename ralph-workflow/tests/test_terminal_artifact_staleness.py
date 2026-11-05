"""A killed agent must not be rescued by an artifact left over from an earlier run."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from ralph.pipeline.effect_executor import (
    _artifact_fingerprint,
    _has_valid_terminal_artifact,
)

PLAN = "# Plan\n"


def _ctx(root: Path) -> SimpleNamespace:
    return SimpleNamespace(workspace_scope=SimpleNamespace(root=root))


def _artifact() -> SimpleNamespace:
    return SimpleNamespace(artifact_path=".agent/artifacts/plan.md", artifact_type="plan")


def _killed() -> Exception:
    exc = RuntimeError("killed")
    exc.returncode = 143  # type: ignore[attr-defined]
    return exc


def test_stale_artifact_not_accepted_after_kill(tmp_path: Path) -> None:
    path = tmp_path / ".agent/artifacts/plan.md"
    path.parent.mkdir(parents=True)
    path.write_text(PLAN)
    ctx, art = _ctx(tmp_path), _artifact()
    baseline = _artifact_fingerprint(ctx, art)  # type: ignore[arg-type]
    assert not _has_valid_terminal_artifact(ctx, art, _killed(), baseline=baseline)  # type: ignore[arg-type]


def test_rewritten_artifact_gets_past_staleness_gate(tmp_path: Path) -> None:
    path = tmp_path / ".agent/artifacts/plan.md"
    path.parent.mkdir(parents=True)
    path.write_text(PLAN)
    ctx, art = _ctx(tmp_path), _artifact()
    baseline = _artifact_fingerprint(ctx, art)  # type: ignore[arg-type]
    path.write_text(PLAN + "revised\n")
    os.utime(path, ns=(1, 2_000_000_000_000_000_000))
    assert _artifact_fingerprint(ctx, art) != baseline  # type: ignore[arg-type]
