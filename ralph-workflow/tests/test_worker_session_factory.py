from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    resolve_capability_profile,
)
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


def test_worker_session_has_unknown_identity(tmp_path: Path) -> None:
    # Worker sessions intentionally have no provider/model context under the
    # current build_worker_session + coordinator + factory API boundary.
    bundle = build_worker_session(_make_unit(), _make_factory(), _make_scope(tmp_path))
    assert bundle.session.model_identity == UNKNOWN_IDENTITY


def test_worker_session_has_unknown_capability_profile(tmp_path: Path) -> None:
    # Capability profile must resolve to unknown-provider defaults, not a
    # known-provider profile, because the worker session carries no identity.
    bundle = build_worker_session(_make_unit(), _make_factory(), _make_scope(tmp_path))
    profile = bundle.session.capability_profile
    assert isinstance(profile, ResolvedCapabilityProfile)
    assert not profile.identity.is_known()


def test_worker_session_inherits_session_contract_params(tmp_path: Path) -> None:
    """Explicit session contract parameters must land verbatim on the worker session."""
    unit = _make_unit("task-contract")
    scope = _make_scope(tmp_path)
    identity = MultimodalModelIdentity(provider="claude", model_id="claude-sonnet-4-20250514")
    profile = resolve_capability_profile(identity)
    capabilities = frozenset({"media.read", "workspace.read", "workspace.edit"})
    artifact_dir = tmp_path / ".agent" / "workers" / "task-contract" / "artifacts"
    artifact_dir.mkdir(parents=True)

    bundle = build_worker_session(
        unit,
        _make_factory(),
        scope,
        worker_artifact_dir=artifact_dir,
        session_drain="development",
        session_capabilities=capabilities,
        session_model_identity=identity,
        session_capability_profile=profile,
    )

    assert bundle.session.drain == "development"
    assert bundle.session.capabilities == capabilities
    assert bundle.session.model_identity == identity
    assert bundle.session.stored_capability_profile == profile


def test_worker_session_drain_defaults_to_empty_without_session_contract(
    tmp_path: Path,
) -> None:
    """When no session contract is provided, drain defaults to empty string."""
    bundle = build_worker_session(_make_unit(), _make_factory(), _make_scope(tmp_path))
    assert bundle.session.drain == ""


def test_worker_session_capabilities_default_to_empty_without_session_contract(
    tmp_path: Path,
) -> None:
    """When no session contract is provided, capabilities default to empty frozenset."""
    bundle = build_worker_session(_make_unit(), _make_factory(), _make_scope(tmp_path))
    assert bundle.session.capabilities == set()


def test_worker_session_uses_unknown_identity_when_not_provided(
    tmp_path: Path,
) -> None:
    """When session_model_identity is None, worker session uses UNKNOWN_IDENTITY."""
    bundle = build_worker_session(_make_unit(), _make_factory(), _make_scope(tmp_path))
    assert bundle.session.model_identity == UNKNOWN_IDENTITY


def test_worker_session_uses_unknown_profile_when_not_provided(tmp_path: Path) -> None:
    """When session_capability_profile is None, capability_profile property resolves."""
    bundle = build_worker_session(_make_unit(), _make_factory(), _make_scope(tmp_path))
    # capability_profile is a property that resolves from model_identity when stored is None
    profile = bundle.session.capability_profile
    assert isinstance(profile, ResolvedCapabilityProfile)
    assert not profile.identity.is_known()
