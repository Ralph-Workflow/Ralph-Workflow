"""Regression tests: web-access MCP tools are visible and callable across all SessionDrains.

These tests verify the historically brittle "configured-but-invisible" regression:
- visit_url (built-in WebVisit) must appear in tools/list for every drain
- visit_url must be callable and return isError=false for every drain
- upstream proxy tools (ralph_upstream__<name>__<tool>) must appear in tools/list
  for every drain that grants UPSTREAM_TOOL_USE
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING, Any

import pytest

from ralph.mcp.protocol import startup
from ralph.mcp.protocol.capability_mapping import Capability, SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.names import VISIT_URL_TOOL, upstream_proxy_tool_name
from ralph.process.manager import ManagedProcess, get_process_manager
from ralph.prompts.template_variables import DEFAULT_CAPABILITIES

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
FAKE_STDIO_MCP = PACKAGE_ROOT / "tests" / "fixtures" / "fake_stdio_mcp.py"

pytestmark = pytest.mark.timeout_seconds(2.5)


class _RunningServer:
    def __init__(self, handle: ManagedProcess, endpoint: str) -> None:
        self.handle = handle
        self.endpoint = endpoint

    def stop(self) -> tuple[str, str]:
        if self.handle.poll() is None:
            with contextlib.suppress(Exception):
                self.handle.terminate(grace_period_s=5.0)
        raw_out, raw_err = self.handle.communicate()

        def _s(v: object) -> str:
            if isinstance(v, bytes):
                return v.decode()
            return v if isinstance(v, str) else ""

        return _s(raw_out), _s(raw_err)


@pytest.fixture(autouse=True)
def _clean_mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RALPH_UPSTREAM_MCP_CONFIG", raising=False)
    monkeypatch.delenv("RALPH_MCP_SESSION_FILE", raising=False)
    monkeypatch.delenv("RALPH_MCP_SESSION_JSON", raising=False)


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(endpoint: str) -> None:
    target = startup.parse_http_endpoint(endpoint)
    deadline = time.monotonic() + 5
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(target.address, timeout=0.1):
                pass
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.01)
    raise AssertionError(f"server failed to start: {last_error}")


def _write_mcp_toml(workspace: Path, body: str) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text(dedent(body).strip() + "\n", encoding="utf-8")


def _fake_stdio_payload(command: str) -> str:
    return json.dumps(
        [
            {
                "name": "fake_crawl",
                "transport": "stdio",
                "url": None,
                "command": command,
                "args": [str(FAKE_STDIO_MCP.resolve())],
                "env": {},
            }
        ]
    )


def _bootstrap_visit_url_mock() -> str:
    """Bootstrap script that patches fetch_url to return a stubbed response."""
    return dedent(
        """
        from __future__ import annotations

        import runpy
        import sys

        # Patch fetch_url before the server module loads
        import ralph.mcp.webvisit.fetcher as fetcher_module

        mock_outcome = fetcher_module.FetchOutcome(
            status="ok",
            effective_url="https://example.com/page",
            http_status=200,
            content_type="text/html; charset=utf-8",
            body=b"<html><body><p>Test content</p></body></html>",
        )
        def patched_fetch(*args: object, **kwargs: object) -> fetcher_module.FetchOutcome:
            return mock_outcome
        fetcher_module.fetch_url = patched_fetch

        # Also patch extract_readable
        import ralph.mcp.webvisit.extractor as extractor_module

        mock_page = extractor_module.ExtractedPage(
            title="Example Page",
            text="Test content",
            links=(),
        )
        def patched_extract(*args: object, **kwargs: object) -> extractor_module.ExtractedPage:
            return mock_page
        extractor_module.extract_readable = patched_extract

        # Now run the actual server
        sys.argv = [
            "ralph.mcp.server",
            "--workspace",
            sys.argv[1],
            "--host",
            "127.0.0.1",
            "--port",
            sys.argv[2],
        ]
        runpy.run_module("ralph.mcp.server", run_name="__main__")
        """
    )


def _bootstrap_plain() -> str:
    """Bootstrap script that runs the server without patching."""
    return dedent(
        """
        from __future__ import annotations
        import runpy
        import sys
        sys.argv = [
            "ralph.mcp.server",
            "--workspace",
            sys.argv[1],
            "--host",
            "127.0.0.1",
            "--port",
            sys.argv[2],
        ]
        runpy.run_module("ralph.mcp.server", run_name="__main__")
        """
    )


@contextmanager
def _run_server(
    workspace: Path,
    *,
    upstream_payload: str | None = None,
    bootstrap_text: str | None = None,
    session_json: str | None = None,
) -> Iterator[_RunningServer]:
    port = _reserve_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PACKAGE_ROOT)
    if upstream_payload is not None:
        env["RALPH_UPSTREAM_MCP_CONFIG"] = upstream_payload
    else:
        env.pop("RALPH_UPSTREAM_MCP_CONFIG", None)
    if session_json is not None:
        env["RALPH_MCP_SESSION_JSON"] = session_json
    else:
        env.pop("RALPH_MCP_SESSION_JSON", None)

    if bootstrap_text is None:
        command = [
            sys.executable,
            "-m",
            "ralph.mcp.server",
            "--workspace",
            str(workspace),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
    else:
        bootstrap = workspace / "bootstrap_mcp_server.py"
        bootstrap.write_text(bootstrap_text, encoding="utf-8")
        command = [sys.executable, str(bootstrap), str(workspace), str(port)]

    handle = get_process_manager().spawn(
        command,
        cwd=str(PACKAGE_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        label="test:web-access-phase-visibility",
    )
    server = _RunningServer(handle=handle, endpoint=endpoint)
    try:
        _wait_for_server(server.endpoint)
        yield server
    finally:
        server.stop()


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield workspace


@pytest.fixture
def fake_upstream_payload() -> str:
    return _fake_stdio_payload(sys.executable)


class TestVisitUrlPhaseVisibility:
    """Test that visit_url is visible and callable for every SessionDrain."""

    @pytest.mark.parametrize("drain_str", [d.value for d in SessionDrain])
    def test_visit_url_listed_for_drain(
        self,
        temp_workspace: Path,
        drain_str: str,
    ) -> None:
        """visit_url must appear in tools/list for every drain."""
        _write_mcp_toml(
            temp_workspace,
            """
            [web_visit]
            enabled = true
            """,
        )

        # Build session with the exact DEFAULT_CAPABILITIES for this drain
        session_drain = SessionDrain(drain_str)
        default_caps = DEFAULT_CAPABILITIES.get(session_drain, ())
        session = AgentSession(
            session_id=f"test-{drain_str}",
            run_id="run-1",
            drain=drain_str,
            capabilities={cap.value for cap in default_caps},
        )

        session_json = json.dumps(
            {
                "session_id": session.session_id,
                "run_id": session.run_id,
                "drain": session.drain,
                "capabilities": list(session.capabilities),
            }
        )

        with _run_server(
            temp_workspace,
            bootstrap_text=_bootstrap_visit_url_mock(),
            session_json=session_json,
        ) as server:
            session_id = _do_initialize(server.endpoint)
            tools = _do_tools_list(server.endpoint, session_id)
            tool_names = {t["name"] for t in tools}

            assert VISIT_URL_TOOL in tool_names, (
                f"visit_url not in tools/list for drain {drain_str}; "
                f"got: {sorted(tool_names)}"
            )

    @pytest.mark.parametrize("drain_str", [d.value for d in SessionDrain])
    def test_visit_url_callable_for_drain(
        self,
        temp_workspace: Path,
        drain_str: str,
    ) -> None:
        """visit_url must be callable and return isError=false for every drain."""
        _write_mcp_toml(
            temp_workspace,
            """
            [web_visit]
            enabled = true
            """,
        )

        session_drain = SessionDrain(drain_str)
        default_caps = DEFAULT_CAPABILITIES.get(session_drain, ())
        session = AgentSession(
            session_id=f"test-{drain_str}",
            run_id="run-1",
            drain=drain_str,
            capabilities={cap.value for cap in default_caps},
        )

        session_json = json.dumps(
            {
                "session_id": session.session_id,
                "run_id": session.run_id,
                "drain": session.drain,
                "capabilities": list(session.capabilities),
            }
        )

        with _run_server(
            temp_workspace,
            bootstrap_text=_bootstrap_visit_url_mock(),
            session_json=session_json,
        ) as server:
            session_id = _do_initialize(server.endpoint)
            _do_initialized_notification(server.endpoint, session_id)
            _do_tools_list(server.endpoint, session_id)

            # Call visit_url
            result = _do_tool_call(
                server.endpoint, session_id, VISIT_URL_TOOL, {"url": "https://example.com/page"}
            )

            assert result.get("isError") is not True, (
                f"visit_url returned error for drain {drain_str}: {result}"
            )
            content = result.get("content", [])
            assert len(content) >= 1
            text_block = content[0]
            assert text_block.get("type") == "text"
            inner = json.loads(text_block["text"])
            assert inner.get("status") == "ok", f"Expected status=ok for drain {drain_str}: {inner}"


class TestUpstreamToolPhaseVisibility:
    """Test that upstream proxy tools are visible for every drain that grants UPSTREAM_TOOL_USE."""

    @pytest.mark.parametrize("drain_str", [d.value for d in SessionDrain])
    def test_upstream_proxy_listed_for_drain(
        self,
        temp_workspace: Path,
        drain_str: str,
        fake_upstream_payload: str,
    ) -> None:
        """Upstream proxy tools must appear in tools/list for drains with UPSTREAM_TOOL_USE."""
        _write_mcp_toml(
            temp_workspace,
            """
            [web_visit]
            enabled = true
            """,
        )

        session_drain = SessionDrain(drain_str)
        default_caps = DEFAULT_CAPABILITIES.get(session_drain, ())
        session = AgentSession(
            session_id=f"test-{drain_str}",
            run_id="run-1",
            drain=drain_str,
            capabilities={cap.value for cap in default_caps},
        )

        proxy_alias = upstream_proxy_tool_name("fake_crawl", "fake_tool")

        session_json = json.dumps(
            {
                "session_id": session.session_id,
                "run_id": session.run_id,
                "drain": session.drain,
                "capabilities": list(session.capabilities),
            }
        )

        with _run_server(
            temp_workspace,
            bootstrap_text=_bootstrap_plain(),
            upstream_payload=fake_upstream_payload,
            session_json=session_json,
        ) as server:
            session_id = _do_initialize(server.endpoint)
            tools = _do_tools_list(server.endpoint, session_id)
            tool_names = {t["name"] for t in tools}

            has_upstream_cap = Capability.UPSTREAM_TOOL_USE in default_caps
            if has_upstream_cap:
                assert proxy_alias in tool_names, (
                    f"upstream proxy {proxy_alias} not in tools/list for drain {drain_str} "
                    f"(which has UPSTREAM_TOOL_USE); got: {sorted(tool_names)}"
                )


def _do_initialize(base_url: str) -> str:
    """Send initialize request and return the session ID."""
    target = startup.parse_http_endpoint(base_url)
    response, session_id = startup.post_http_jsonrpc_with_session(
        base_url,
        target,
        startup.initialize_request(),
    )
    assert response["result"]
    assert session_id
    startup.post_http_jsonrpc_with_session(
        base_url,
        target,
        startup.initialized_notification(),
        session_id=session_id,
    )
    return session_id


def _do_initialized_notification(base_url: str, session_id: str) -> None:
    """Send notifications/initialized and expect 202 Accepted."""
    target = startup.parse_http_endpoint(base_url)
    response, _ = startup.post_http_jsonrpc_with_session(
        base_url,
        target,
        startup.initialized_notification(),
        session_id=session_id,
    )
    # 202 Accepted is expected for notifications
    assert response is not None


def _do_tools_list(base_url: str, session_id: str) -> list[dict[str, Any]]:
    """Send tools/list request and return the tools array."""
    target = startup.parse_http_endpoint(base_url)
    response, _ = startup.post_http_jsonrpc_with_session(
        base_url,
        target,
        startup.tools_list_request(),
        session_id=session_id,
    )
    result = response["result"]
    assert isinstance(result, dict)
    tools = result["tools"]
    assert isinstance(tools, list)
    return tools


def _do_tool_call(
    base_url: str,
    session_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Send tools/call request and return the result."""
    target = startup.parse_http_endpoint(base_url)
    response, _ = startup.post_http_jsonrpc_with_session(
        base_url,
        target,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        session_id=session_id,
    )
    return response
