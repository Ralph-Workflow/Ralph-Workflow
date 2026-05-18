"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    resolve_capability_profile,
)
from ralph.mcp.server.factory import McpServerHandle
from ralph.pipeline.parallel.coordinator import (
    prepare_executor,
)
from ralph.pipeline.parallel.mode import SameWorkspaceContext
from ralph.pipeline.work_units import (
    WorkUnit,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class TestPrepareExecutorSameWorkspace:
    class _FakeMcpServerFactory:
        def __init__(self) -> None:
            self.build = MagicMock(
                side_effect=lambda session: McpServerHandle(
                    endpoint="http://127.0.0.1:9999/mcp",
                    pid=99999,
                    shutdown=lambda: None,
                )
            )

    class _SessionContract:
        def __init__(
            self,
            *,
            drain: str,
            capabilities: frozenset[str],
            model_identity: object,
            capability_profile: object,
        ) -> None:
            self.drain = drain
            self.capabilities = capabilities
            self.model_identity = model_identity
            self.capability_profile = capability_profile

    def test_inprocess_uses_injected_mcp_factory(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _executor, bundle, worker_namespace = prepare_executor(unit, mock_executor, ctx)

        # injected factory must be used, not a new one
        assert ctx.mcp_factory.build.called
        assert bundle is not None
        assert worker_namespace is not None

    def test_inprocess_creates_worker_namespace_subdirs(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        prepare_executor(unit, mock_executor, ctx)

        namespace = ctx.worker_namespace_root / "unit-a"
        for subdir in ("artifacts", "tmp", "logs", "handoffs"):
            assert (namespace / subdir).is_dir(), f"Expected {subdir}/ to exist"

    def test_worker_artifact_dir_set_on_session(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _executor, bundle, worker_namespace = prepare_executor(unit, mock_executor, ctx)

        assert bundle is not None
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


_FakeMcpServerFactory = TestPrepareExecutorSameWorkspace._FakeMcpServerFactory
_SessionContract = TestPrepareExecutorSameWorkspace._SessionContract


def _make_same_workspace_context(
    tmp_path: Path,
    *,
    executor_command: tuple[str, ...] | None = None,
    session_contract: _SessionContract | None = None,
) -> SameWorkspaceContext:
    return SameWorkspaceContext(
        repo_root=tmp_path,
        mcp_factory=_FakeMcpServerFactory(),
        executor_command=executor_command,
        session_drain=session_contract.drain if session_contract else "",
        session_capabilities=session_contract.capabilities if session_contract else frozenset(),
        session_model_identity=session_contract.model_identity if session_contract else None,
        session_capability_profile=(
            session_contract.capability_profile if session_contract else None
        ),
    )
