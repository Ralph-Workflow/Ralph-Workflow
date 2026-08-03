"""Integration tests for the standalone Python MCP server runtime."""

# Property A: there is no alternate FastMCP path. The single production
# _FallbackStandaloneServer (via build_standalone_http_server) is the
# only server construction surface. This test pins the shipped path.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from loguru import logger

# Config imports for multimodal tests
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    DeliveryMode,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
)
from ralph.mcp.protocol import startup
from ralph.mcp.protocol.capability_mapping import McpCapability
from ralph.mcp.protocol.env import MCP_SESSION_ENV, WORKER_ARTIFACT_DIR_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.server.runtime import FileBackedSession
from ralph.mcp.tools.names import upstream_proxy_tool_name
from ralph.mcp.upstream.client import HttpUpstreamClient, StdioUpstreamClient, make_upstream_client
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
)
from ralph.mcp.upstream.models import UpstreamCallError
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.phases import PhaseContext
from ralph.phases.execution import handle_execution_phase
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from tests._support.typed_accessors import (
    must_dict_list,
    must_mapping,
    must_str,
)
from tests.mcp.test_md_plan_spec import _plan_document

# Lazy imports for multimodal tests that require optional dependencies
# These are only available when the multimodal feature is fully configured
_lazy_imports: dict[str, object] = {}

HTTP_OK = 200
HTTP_ACCEPTED = 202


@pytest.fixture(autouse=True)
def _isolate_from_upstream_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prevent real upstream MCP servers (configured in dev env) from being
    # loaded during tests. Each test provides its own upstream config if needed.
    monkeypatch.delenv(UPSTREAM_MCP_CONFIG_ENV, raising=False)


def _session(run_id: str = "run-1", capabilities: set[str] | None = None) -> AgentSession:
    return AgentSession(
        session_id=f"session-{run_id}",
        run_id=run_id,
        drain="development",
        capabilities=capabilities
        or {
            "RunReportProgress",
            "ArtifactSubmit",
            "EnvRead",
            "WorkspaceRead",
        },
    )


def _http_call(
    endpoint: str, method: str, params: dict[str, object] | None = None, *, msg_id: int = 1
) -> dict[str, object]:
    target = startup.parse_http_endpoint(endpoint)
    return startup.post_http_jsonrpc(
        target,
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        },
    )


# =============================================================================
# Image content serialization tests (Task 3)
# =============================================================================


class TestFileBackedSessionWorkerArtifactDir:
    def test_worker_artifact_dir_returns_path_when_env_set(self, tmp_path: Path) -> None:

        session_file = tmp_path / "session.json"
        session_file.write_text(
            '{"session_id":"s","run_id":"r","drain":"d","capabilities":[]}',
            encoding="utf-8",
        )
        session = FileBackedSession(
            session_file,
            env_getter=lambda k: "/tmp/artifacts" if k == WORKER_ARTIFACT_DIR_ENV else None,
        )
        assert session.worker_artifact_dir == Path("/tmp/artifacts")

    def test_worker_artifact_dir_returns_none_when_env_not_set(self, tmp_path: Path) -> None:

        session_file = tmp_path / "session.json"
        session_file.write_text(
            '{"session_id":"s","run_id":"r","drain":"d","capabilities":[]}',
            encoding="utf-8",
        )
        session = FileBackedSession(session_file, env_getter=lambda k: None)
        assert session.worker_artifact_dir is None

    def test_file_backed_session_restores_parallel_worker_edit_roots(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "repo"
        worker_ns = repo_root / ".agent" / "workers" / "unit-a"
        allowed_dir = repo_root / "src" / "unit-a"
        session_dir = repo_root / ".agent" / "tmp"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "session.json"
        session_file.write_text(
            json.dumps(
                {
                    "session_id": "s",
                    "run_id": "r",
                    "drain": "d",
                    "capabilities": [],
                    "parallel_worker": True,
                    "worker_artifact_dir": str(worker_ns / "artifacts"),
                    "worker_namespace": str(worker_ns),
                    "allowed_roots": [str(allowed_dir), str(worker_ns)],
                }
            ),
            encoding="utf-8",
        )

        session = FileBackedSession(session_file, env_getter=lambda _k: None)

        assert session.is_parallel_worker() is True
        assert session.worker_artifact_dir == worker_ns / "artifacts"
        assert session.worker_namespace == worker_ns
        assert session.check_edit_area("src/unit-a/file.txt") == "approved"
        assert session.check_edit_area("src/other/file.txt") == "denied"
