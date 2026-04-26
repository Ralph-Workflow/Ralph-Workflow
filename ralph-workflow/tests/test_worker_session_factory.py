from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.server.factory import McpServerHandle
from ralph.pipeline.parallel.worker_session import build_worker_session
from ralph.pipeline.work_units import WorkUnit
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path


def _make_unit(unit_id: str = "task-alpha") -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description="test unit")


def _make_handle() -> McpServerHandle:
    return McpServerHandle(endpoint="http://localhost:9999", pid=1234, shutdown=lambda: None)


class FakeMcpFactory:
    def __init__(self, handle: McpServerHandle) -> None:
        self._handle = handle

    def build(self, _session: object) -> McpServerHandle:
        return self._handle


def _make_factory() -> FakeMcpFactory:
    return FakeMcpFactory(_make_handle())


def _make_scope(tmp_path: Path) -> WorkspaceScope:
    return WorkspaceScope(root=tmp_path)


def test_parallel_worker_flag(tmp_path: Path) -> None:
    bundle = build_worker_session(_make_unit(), _make_factory(), _make_scope(tmp_path))
    assert bundle.session.parallel_worker is True


def test_unique_session_ids(tmp_path: Path) -> None:
    unit = _make_unit()
    scope = _make_scope(tmp_path)
    bundle1 = build_worker_session(unit, _make_factory(), scope)
    bundle2 = build_worker_session(unit, _make_factory(), scope)
    assert bundle1.session.session_id != bundle2.session.session_id


def test_work_unit_id_set(tmp_path: Path) -> None:
    unit = _make_unit("task-beta")
    bundle = build_worker_session(unit, _make_factory(), _make_scope(tmp_path))
    assert "task-beta" in bundle.session.session_id


def test_bundle_frozen(tmp_path: Path) -> None:
    bundle = build_worker_session(_make_unit(), _make_factory(), _make_scope(tmp_path))
    with pytest.raises(FrozenInstanceError):
        bundle.session = None  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_mcp_handle_stored(tmp_path: Path) -> None:
    handle = _make_handle()
    factory = FakeMcpFactory(handle)
    bundle = build_worker_session(_make_unit(), factory, _make_scope(tmp_path))
    assert bundle.mcp_handle is handle


def test_workspace_scope_rooted_at_repo(tmp_path: Path) -> None:
    unit = _make_unit("task-gamma")
    worker_ns = tmp_path / ".agent" / "workers" / "task-gamma"
    worker_ns.mkdir(parents=True)
    scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=tmp_path,
        allowed_directories=("src",),
        worker_namespace=worker_ns,
    )
    bundle = build_worker_session(unit, _make_factory(), scope)
    assert bundle.workspace_scope.root == tmp_path.resolve()


def test_allowed_directories_forwarded(tmp_path: Path) -> None:
    unit = WorkUnit(unit_id="task-delta", description="d", allowed_directories=["src", "tests"])
    worker_ns = tmp_path / ".agent" / "workers" / "task-delta"
    worker_ns.mkdir(parents=True)
    scope = WorkspaceScope.for_same_workspace_worker(
        repo_root=tmp_path,
        allowed_directories=("src", "tests"),
        worker_namespace=worker_ns,
    )
    bundle = build_worker_session(unit, _make_factory(), scope)
    repo_root = bundle.workspace_scope.root
    assert repo_root / "src" in bundle.workspace_scope.allowed_roots
    assert repo_root / "tests" in bundle.workspace_scope.allowed_roots


def test_worker_artifact_dir_stored_in_session(tmp_path: Path) -> None:
    unit = _make_unit("task-epsilon")
    artifact_dir = tmp_path / ".agent" / "workers" / "task-epsilon" / "artifacts"
    artifact_dir.mkdir(parents=True)
    bundle = build_worker_session(
        unit, _make_factory(), _make_scope(tmp_path), worker_artifact_dir=artifact_dir
    )
    assert bundle.session.worker_artifact_dir == artifact_dir
