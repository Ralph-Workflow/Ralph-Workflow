"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    resolve_capability_profile,
)
from ralph.pipeline.parallel.coordinator import prepare_executor
from ralph.pipeline.parallel.mode import SameWorkspaceContext
from ralph.pipeline.work_units import WorkUnit

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from tests._prepare_executor_fake_mcp_server_factory import _FakeMcpServerFactory
from tests._prepare_session_contract import _SessionContract


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class TestPrepareExecutorSameWorkspace:
    def test_inprocess_uses_injected_mcp_factory(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _executor, bundle, worker_namespace = prepare_executor(unit, mock_executor, ctx)

        # injected factory must be used, not a new one
        assert cast("MagicMock", ctx.mcp_factory.build).called
        assert bundle is not None
        assert worker_namespace is not None

    def test_inprocess_creates_worker_namespace_subdirs(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        prepare_executor(unit, mock_executor, ctx)

        assert ctx.worker_namespace_root is not None
        namespace = ctx.worker_namespace_root / "unit-a"
        for subdir in ("artifacts", "tmp", "logs", "handoffs"):
            assert (namespace / subdir).is_dir(), f"Expected {subdir}/ to exist"

    def test_worker_artifact_dir_set_on_session(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _executor, bundle, worker_namespace = prepare_executor(unit, mock_executor, ctx)

        assert bundle is not None
        assert worker_namespace is not None
        assert bundle.session.worker_artifact_dir == worker_namespace / "artifacts"

    def test_no_same_workspace_context_returns_original_executor(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()

        returned_executor, bundle, worker_namespace = prepare_executor(unit, mock_executor, None)

        assert returned_executor is mock_executor
        assert bundle is None
        assert worker_namespace is None

    def test_inprocess_forwards_session_contract_to_worker_session(self, tmp_path: Path) -> None:
        """In-process _prepare_executor forwards session contract fields to build_worker_session."""
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        identity = MultimodalModelIdentity(provider="claude", model_id="claude-sonnet-4")
        profile = resolve_capability_profile(identity)
        capabilities = frozenset({"media.read", "workspace.edit"})
        contract = _SessionContract(
            drain="development",
            capabilities=capabilities,
            model_identity=identity,
            capability_profile=profile,
        )
        ctx = _make_same_workspace_context(
            tmp_path,
            executor_command=None,
            session_contract=contract,
        )

        _executor, bundle, _worker_namespace = prepare_executor(unit, mock_executor, ctx)

        assert bundle is not None
        assert bundle.session.drain == "development"
        assert bundle.session.capabilities == capabilities
        assert bundle.session.model_identity == identity
        assert bundle.session.stored_capability_profile == profile

    def test_inprocess_worker_session_has_unknown_identity_when_context_has_none(
        self, tmp_path: Path
    ) -> None:
        """When session_model_identity is None, worker session uses UNKNOWN_IDENTITY."""
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _executor, bundle, _worker_namespace = prepare_executor(unit, mock_executor, ctx)

        assert bundle is not None
        assert bundle.session.model_identity.provider == "unknown"

    def test_subprocess_executor_command_includes_parallel_worker_manifest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        manifest_path = tmp_path / ".agent" / "workers" / "unit-a" / "worker-manifest.json"
        ctx = _make_same_workspace_context(
            tmp_path,
            executor_command=("python", "-m", "ralph"),
            worker_commands={
                "unit-a": (
                    "python",
                    "-m",
                    "ralph",
                    "--parallel-worker-manifest",
                    str(manifest_path),
                )
            },
            worker_manifest_paths={"unit-a": manifest_path},
        )
        seen: dict[str, object] = {}

        class _FakeSubprocessExecutor:
            def __init__(self, command: object, **kwargs: object) -> None:
                seen["command"] = command
                seen["kwargs"] = kwargs

        class _FakeDynamicBindingMcpServerFactory(_FakeMcpServerFactory):
            def __init__(self, workspace: object) -> None:
                del workspace
                super().__init__()

        monkeypatch.setattr(
            "ralph.pipeline.parallel.parallel_coordinator.subprocess_executor.SubprocessAgentExecutor",
            _FakeSubprocessExecutor,
        )
        monkeypatch.setattr(
            "ralph.pipeline.parallel.parallel_coordinator.factory_impl.DynamicBindingMcpServerFactory",
            _FakeDynamicBindingMcpServerFactory,
        )

        returned_executor, _bundle, _worker_namespace = prepare_executor(unit, mock_executor, ctx)

        assert returned_executor is not mock_executor
        assert seen["command"] == (
            "python",
            "-m",
            "ralph",
            "--parallel-worker-manifest",
            str(manifest_path),
        )


def _make_same_workspace_context(
    tmp_path: Path,
    *,
    executor_command: tuple[str, ...] | None = None,
    session_contract: _SessionContract | None = None,
    worker_commands: dict[str, tuple[str, ...]] | None = None,
    worker_manifest_paths: dict[str, Path] | None = None,
) -> SameWorkspaceContext:
    session_drain = session_contract.drain if session_contract is not None else ""
    session_capabilities = (
        session_contract.capabilities if session_contract is not None else frozenset()
    )
    session_model_identity = (
        cast("MultimodalModelIdentity | None", session_contract.model_identity)
        if session_contract is not None
        else None
    )
    session_capability_profile = (
        cast("ResolvedCapabilityProfile | None", session_contract.capability_profile)
        if session_contract is not None
        else None
    )
    return SameWorkspaceContext(
        repo_root=tmp_path,
        mcp_factory=_FakeMcpServerFactory(),
        executor_command=executor_command,
        worker_commands=worker_commands or {},
        worker_manifest_paths=worker_manifest_paths or {},
        session_drain=session_drain,
        session_capabilities=session_capabilities,
        session_model_identity=session_model_identity,
        session_capability_profile=session_capability_profile,
    )
