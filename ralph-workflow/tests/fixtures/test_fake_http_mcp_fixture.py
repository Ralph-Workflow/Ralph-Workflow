from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.startup import (
    initialize_request,
    initialized_notification,
    parse_http_endpoint,
    post_http_jsonrpc_with_session,
    tools_list_request,
)
from ralph.process.manager import ProcessTerminationError, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def _spawn_fake_http_mcp() -> Iterator[int]:
    handle = get_process_manager().spawn(
        [sys.executable, "-m", "tests.fixtures.fake_http_mcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        label="test:fake-http-mcp",
    )
    try:
        stdout = handle.stdout
        assert stdout is not None
        port_line = stdout.readline().strip()
        if not port_line:
            raise AssertionError("fake_http_mcp did not print its port before exiting")
        yield int(port_line)
    finally:
        with contextlib.suppress(ProcessTerminationError):
            handle.terminate(grace_period_s=5.0)
        if handle.stdout is not None:
            with contextlib.suppress(Exception):
                handle.stdout.close()
        if handle.stderr is not None:
            with contextlib.suppress(Exception):
                handle.stderr.close()


class TestFakeHttpMcp:
    def test_initialize_then_tools_list_roundtrip(self) -> None:
        with _spawn_fake_http_mcp() as port:
            endpoint = f"http://127.0.0.1:{port}/mcp"
            target = parse_http_endpoint(endpoint)

            deadline = time.monotonic() + 5
            last_error: Exception | None = None
            initialize_response: dict[str, object] | None = None
            session_id: str | None = None
            while time.monotonic() < deadline:
                try:
                    initialize_response, session_id = post_http_jsonrpc_with_session(
                        endpoint, target, initialize_request()
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.05)
            if initialize_response is None:
                raise AssertionError(f"fake_http_mcp never accepted a request: {last_error}")

            assert session_id
            assert initialize_response["id"] == 1
            result = initialize_response["result"]
            assert isinstance(result, dict)
            server_info = result["serverInfo"]
            assert isinstance(server_info, dict)
            assert server_info["name"] == "fake-http-mcp"

            post_http_jsonrpc_with_session(
                endpoint, target, initialized_notification(), session_id=session_id
            )

            tools_response, _ = post_http_jsonrpc_with_session(
                endpoint, target, tools_list_request(), session_id=session_id
            )
            tools_result = tools_response["result"]
            assert isinstance(tools_result, dict)
            tools = tools_result["tools"]
            assert isinstance(tools, list)
            assert len(tools) == 1
            first = tools[0]
            assert isinstance(first, dict)
            assert first["name"] == "fake_tool"


pytestmark = pytest.mark.timeout_seconds(15)
