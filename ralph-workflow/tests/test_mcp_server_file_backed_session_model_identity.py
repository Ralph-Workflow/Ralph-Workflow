"""Integration tests for the standalone Python MCP server runtime."""

# Property A: there is no alternate FastMCP path. The single production
# _FallbackStandaloneServer (via build_standalone_http_server) is the
# only server construction surface. This test pins the shipped path.

from __future__ import annotations

import json
import json as _json
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
from ralph.mcp.protocol.env import MCP_SESSION_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.server.lifecycle import session_payload_json
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


class TestFileBackedSessionModelIdentity:
    """Tests for FileBackedSession.model_identity property."""

    def test_file_backed_session_restores_known_model_identity(self, tmp_path: Path) -> None:
        """FileBackedSession.model_identity returns the deserialized MultimodalModelIdentity."""

        payload = {
            "session_id": "sid-fbs",
            "run_id": "run-fbs",
            "drain": "development",
            "capabilities": ["WorkspaceRead"],
            "model_identity": {
                "provider": "anthropic",
                "model_id": "claude-3-5-sonnet",
                "transport": "cli",
            },
        }
        session_file = tmp_path / "session.json"
        session_file.write_text(json.dumps(payload), encoding="utf-8")

        session = FileBackedSession(session_file)
        identity = session.model_identity
        assert isinstance(identity, MultimodalModelIdentity)
        assert identity.provider == "anthropic"
        assert identity.model_id == "claude-3-5-sonnet"
        assert identity.transport == "cli"

    def test_file_backed_session_falls_back_to_unknown_identity_when_absent(
        self, tmp_path: Path
    ) -> None:
        """FileBackedSession.model_identity returns UNKNOWN_IDENTITY when payload omits it."""

        payload = {
            "session_id": "sid-no-mi",
            "run_id": "run-no-mi",
            "drain": "development",
            "capabilities": ["WorkspaceRead"],
        }
        session_file = tmp_path / "session.json"
        session_file.write_text(json.dumps(payload), encoding="utf-8")

        session = FileBackedSession(session_file)
        identity = session.model_identity
        assert identity == UNKNOWN_IDENTITY

    def test_lifecycle_payload_roundtrip_preserves_model_identity(self, tmp_path: Path) -> None:
        """session_payload_json + FileBackedSession restores the same model identity."""

        agent_session = AgentSession(
            session_id="sid-rt",
            run_id="run-rt",
            drain="development",
            capabilities={"WorkspaceRead"},
            model_identity=MultimodalModelIdentity(
                provider="anthropic", model_id="claude-opus-4-7", transport="api"
            ),
        )
        payload_str = session_payload_json(agent_session)
        session_file = tmp_path / "session-rt.json"
        session_file.write_text(payload_str, encoding="utf-8")

        restored = FileBackedSession(session_file)
        identity = restored.model_identity
        assert isinstance(identity, MultimodalModelIdentity)
        assert identity.provider == "anthropic"
        assert identity.model_id == "claude-opus-4-7"
        assert identity.transport == "api"
        # Verify the raw payload also contains model_identity
        raw = _json.loads(payload_str)
        assert raw["model_identity"]["provider"] == "anthropic"
