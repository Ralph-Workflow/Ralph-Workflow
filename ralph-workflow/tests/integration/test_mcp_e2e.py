from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING, Any

import pytest

from ralph.mcp.protocol import startup
from ralph.mcp.tools.names import WEB_SEARCH_TOOL, upstream_proxy_tool_name
from ralph.mcp.transport.common import merge_mcp_toml_into_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer

if TYPE_CHECKING:
    from collections.abc import Iterator

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
FAKE_STDIO_MCP = PACKAGE_ROOT / "tests" / "fixtures" / "fake_stdio_mcp.py"
BROKEN_CANARY = "sk-fake-leak-canary-test-12345"
pytestmark = pytest.mark.timeout_seconds(2.5)


class _RunningServer:
    def __init__(self, process: subprocess.Popen[str], endpoint: str) -> None:
        self.process = process
        self.endpoint = endpoint

    def stop(self) -> tuple[str, str]:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        stdout, stderr = self.process.communicate()
        return stdout, stderr


@pytest.fixture(autouse=True)
def _clean_mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RALPH_UPSTREAM_MCP_CONFIG", raising=False)
    # Prevent the parent Ralph session (if any) from leaking restricted
    # capabilities into the standalone server subprocess via session_from_env.
    monkeypatch.delenv("RALPH_MCP_SESSION_FILE", raising=False)
    monkeypatch.delenv("RALPH_MCP_SESSION_JSON", raising=False)


@contextmanager
def _run_server(
    workspace: Path,
    *,
    upstream_payload: str | None = None,
    bootstrap_text: str | None = None,
) -> Iterator[_RunningServer]:
    port = _reserve_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PACKAGE_ROOT)
    if upstream_payload is not None:
        env["RALPH_UPSTREAM_MCP_CONFIG"] = upstream_payload
    else:
        env.pop("RALPH_UPSTREAM_MCP_CONFIG", None)

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

    process = subprocess.Popen(
        command,
        cwd=str(PACKAGE_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    server = _RunningServer(process=process, endpoint=endpoint)
    try:
        _wait_for_server(server.endpoint)
        yield server
    finally:
        server.stop()


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
        except Exception as exc:  # pragma: no cover - exercised by retry loop
            last_error = exc
            time.sleep(0.01)
    raise AssertionError(f"server failed to start: {last_error}")


def _initialize_session(endpoint: str) -> str:
    target = startup.parse_http_endpoint(endpoint)
    response, session_id = startup.post_http_jsonrpc_with_session(
        endpoint,
        target,
        startup.initialize_request(),
    )
    assert response["result"]
    assert session_id
    startup.post_http_jsonrpc_with_session(
        endpoint,
        target,
        startup.initialized_notification(),
        session_id=session_id,
    )
    return session_id


def _tools_list(endpoint: str, session_id: str) -> list[dict[str, Any]]:
    target = startup.parse_http_endpoint(endpoint)
    response, _ = startup.post_http_jsonrpc_with_session(
        endpoint,
        target,
        startup.tools_list_request(),
        session_id=session_id,
    )
    result = response["result"]
    assert isinstance(result, dict)
    tools = result["tools"]
    assert isinstance(tools, list)
    return tools


def _tool_call(
    endpoint: str,
    session_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    msg_id: int = 3,
) -> dict[str, Any]:
    target = startup.parse_http_endpoint(endpoint)
    response, _ = startup.post_http_jsonrpc_with_session(
        endpoint,
        target,
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        session_id=session_id,
    )
    return response


def _write_mcp_toml(workspace: Path, body: str) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text(dedent(body).strip() + "\n", encoding="utf-8")


def _fake_stdio_payload(command: str) -> str:
    return json.dumps(
        [
            {
                "name": "fake_stdio",
                "transport": "stdio",
                "url": None,
                "command": command,
                "args": [str(FAKE_STDIO_MCP.resolve())],
                "env": {},
            }
        ]
    )


def _mocked_ddgs_bootstrap() -> str:
    return dedent(
        """
        from __future__ import annotations

        import runpy
        import sys
        import types

        fake_ddgs = types.ModuleType("ddgs")

        class DDGS:
            def text(self, query: str, max_results: int):
                return [
                    {
                        "title": f"Mocked Title {index + 1} for {query}",
                        "href": f"https://example.com/{index + 1}",
                        "body": f"Snippet {index + 1}",
                    }
                    for index in range(max_results)
                ]

        fake_ddgs.DDGS = DDGS
        sys.modules["ddgs"] = fake_ddgs
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


def test_mcp_server_boots_with_mcp_toml_and_custom_upstream(tmp_path: Path) -> None:
    _write_mcp_toml(
        tmp_path,
        """
        [web_search]
        enabled = true
        """,
    )

    with _run_server(tmp_path, upstream_payload=_fake_stdio_payload(sys.executable)) as server:
        session_id = _initialize_session(server.endpoint)
        tool_names = {tool["name"] for tool in _tools_list(server.endpoint, session_id)}

    assert WEB_SEARCH_TOOL in tool_names
    assert upstream_proxy_tool_name("fake_stdio", "fake_tool") in tool_names


def test_web_search_end_to_end_with_ddgs(tmp_path: Path) -> None:
    _write_mcp_toml(
        tmp_path,
        """
        [web_search]
        enabled = true
        backend = "ddgs"
        """,
    )

    with _run_server(tmp_path, bootstrap_text=_mocked_ddgs_bootstrap()) as server:
        session_id = _initialize_session(server.endpoint)
        response = _tool_call(
            server.endpoint,
            session_id,
            WEB_SEARCH_TOOL,
            {"query": "test", "limit": 2},
        )

    result = response["result"]
    assert isinstance(result, dict)
    assert result["isError"] is False
    content = result["content"]
    assert isinstance(content, list)
    rendered = json.dumps(content)
    assert "Mocked Title 1 for test" in rendered
    assert "Mocked Title 2 for test" in rendered


def test_web_search_fallback_chain_under_real_config(tmp_path: Path) -> None:
    _write_mcp_toml(
        tmp_path,
        """
        [web_search]
        enabled = true
        backend = "ddgs"
        fallback = ["ddgs"]
        """,
    )

    with _run_server(tmp_path, upstream_payload=_fake_stdio_payload(sys.executable)) as server:
        session_id = _initialize_session(server.endpoint)
        tool_names = {tool["name"] for tool in _tools_list(server.endpoint, session_id)}

    assert WEB_SEARCH_TOOL in tool_names
    assert upstream_proxy_tool_name("fake_stdio", "fake_tool") in tool_names


def test_disabled_web_search_omits_tool(tmp_path: Path) -> None:
    _write_mcp_toml(
        tmp_path,
        """
        [web_search]
        enabled = false
        """,
    )

    with _run_server(tmp_path) as server:
        session_id = _initialize_session(server.endpoint)
        tool_names = {tool["name"] for tool in _tools_list(server.endpoint, session_id)}

    assert WEB_SEARCH_TOOL not in tool_names


def test_unreachable_upstream_emits_warning_and_server_starts(tmp_path: Path) -> None:
    _write_mcp_toml(
        tmp_path,
        """
        [web_search]
        enabled = true
        """,
    )

    broken_payload = _fake_stdio_payload("/definitely/not/a/real/command")
    with _run_server(tmp_path, upstream_payload=broken_payload) as server:
        session_id = _initialize_session(server.endpoint)
        tool_names = {tool["name"] for tool in _tools_list(server.endpoint, session_id)}
        stdout, stderr = server.stop()

    assert WEB_SEARCH_TOOL in tool_names
    assert not any(name.startswith("ralph_upstream__fake_stdio__") for name in tool_names)
    assert "Skipping upstream MCP server fake_stdio" in stdout + stderr


def test_mcp_toml_wins_over_simulated_claude_json_collision() -> None:
    claude_native = (
        UpstreamMcpServer(
            name="shared",
            transport="http",
            url="https://claude.example/mcp",
        ),
    )
    mcp_toml_servers = (
        UpstreamMcpServer(
            name="shared",
            transport="stdio",
            command=sys.executable,
            args=("custom.py",),
        ),
    )

    merged = merge_mcp_toml_into_upstreams(claude_native, mcp_toml_servers)

    assert merged == mcp_toml_servers


def test_secret_never_in_e2e_logs(tmp_path: Path) -> None:
    _write_mcp_toml(
        tmp_path,
        f"""
        [web_search]
        enabled = true
        backend = "ddgs"

        [web_search.backends.tavily]
        backend = "tavily"
        api_key = "{BROKEN_CANARY}"
        """,
    )

    with _run_server(tmp_path) as server:
        session_id = _initialize_session(server.endpoint)
        _tools_list(server.endpoint, session_id)
        stdout, stderr = server.stop()

    assert BROKEN_CANARY not in stdout
    assert BROKEN_CANARY not in stderr
