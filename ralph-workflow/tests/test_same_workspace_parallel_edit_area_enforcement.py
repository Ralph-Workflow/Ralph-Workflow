"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.work_units import (
    WorkUnit,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path



def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class TestEditAreaEnforcement:
    def test_write_inside_declared_dir_succeeds(self, tmp_path: Path) -> None:
        worker_ns = tmp_path / ".agent" / "workers" / "unit-x"
        worker_ns.mkdir(parents=True)
        (tmp_path / "src" / "foo").mkdir(parents=True)

        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/foo",),
            worker_namespace=worker_ns,
        )
        workspace = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)
        # Should succeed
        workspace.write("src/foo/output.txt", "hello")
        assert (tmp_path / "src" / "foo" / "output.txt").read_text() == "hello"

    def test_write_outside_declared_dir_denied(self, tmp_path: Path) -> None:
        worker_ns = tmp_path / ".agent" / "workers" / "unit-x"
        worker_ns.mkdir(parents=True)
        (tmp_path / "src" / "bar").mkdir(parents=True)

        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/foo",),
            worker_namespace=worker_ns,
        )
        workspace = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)
        with pytest.raises(ValueError, match="outside workspace root"):
            workspace.write("src/bar/output.txt", "should fail")

    def test_write_to_worker_namespace_succeeds(self, tmp_path: Path) -> None:
        worker_ns = tmp_path / ".agent" / "workers" / "unit-x"
        (worker_ns / "artifacts").mkdir(parents=True)

        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/foo",),
            worker_namespace=worker_ns,
        )
        workspace = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)
        # Writing to the per-worker namespace must succeed
        workspace.write(".agent/workers/unit-x/artifacts/plan.json", "{}")
        assert (worker_ns / "artifacts" / "plan.json").exists()

    def test_write_to_shared_agent_artifacts_denied(self, tmp_path: Path) -> None:
        worker_ns = tmp_path / ".agent" / "workers" / "unit-x"
        worker_ns.mkdir(parents=True)
        (tmp_path / ".agent" / "artifacts").mkdir(parents=True)

        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/foo",),
            worker_namespace=worker_ns,
        )
        workspace = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)
        with pytest.raises(ValueError, match="outside workspace root"):
            workspace.write(".agent/artifacts/plan.json", "should fail")
