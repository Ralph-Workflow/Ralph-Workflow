from __future__ import annotations

import os
from pathlib import Path

from ralph.phases.artifact_freshness import (
    is_stale_since_invocation,
    record_invocation_baseline,
)
from ralph.workspace.fs import FsWorkspace

ART = ".agent/artifacts/x.md"


def _ws(tmp_path: Path) -> FsWorkspace:
    ws = FsWorkspace(tmp_path)
    ws.write(ART, "old")
    return ws


def test_untouched_artifact_is_stale(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    record_invocation_baseline(ws, "development", ART)
    assert is_stale_since_invocation(ws, "development", ART)


def test_rewritten_artifact_is_fresh(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    record_invocation_baseline(ws, "development", ART)
    (tmp_path / ART).write_text("new")
    os.utime(tmp_path / ART, ns=(1, 2_000_000_000_000_000_000))
    assert not is_stale_since_invocation(ws, "development", ART)


def test_artifact_created_during_run_is_fresh(tmp_path: Path) -> None:
    ws = FsWorkspace(tmp_path)
    record_invocation_baseline(ws, "review", ART)
    ws.write(ART, "new")
    assert not is_stale_since_invocation(ws, "review", ART)


def test_fails_open_without_baseline_or_for_other_path(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert not is_stale_since_invocation(ws, "development", ART)
    record_invocation_baseline(ws, "development", ART)
    assert not is_stale_since_invocation(ws, "development", ".agent/artifacts/other.md")


def test_baseline_is_per_phase(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    record_invocation_baseline(ws, "development", ART)
    assert not is_stale_since_invocation(ws, "review", ART)
