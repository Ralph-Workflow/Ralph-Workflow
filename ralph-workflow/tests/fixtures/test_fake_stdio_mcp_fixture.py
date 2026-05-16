from __future__ import annotations

import json
import subprocess
import sys

from ralph.process.manager import SpawnOptions, get_process_manager


class TestFakeStdioMcp:
    TOOLS_LIST_ID = 2

    def test_initialize_then_tools_list_roundtrip(self) -> None:
        handle = get_process_manager().spawn(
            [sys.executable, "-m", "tests.fixtures.fake_stdio_mcp"],
            SpawnOptions(
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                label="test:fake-stdio-mcp",
            ),
        )
        stdin = handle.stdin
        stdout = handle.stdout
        assert stdin is not None
        assert stdout is not None
        with handle, stdin, stdout:
            initialize_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "test", "version": "0.1.0"},
                },
            }
            print(json.dumps(initialize_request), file=stdin, flush=True)

            initialized_notification = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
            print(json.dumps(initialized_notification), file=stdin, flush=True)

            tools_list_request = {
                "jsonrpc": "2.0",
                "id": self.TOOLS_LIST_ID,
                "method": "tools/list",
                "params": {},
            }
            print(json.dumps(tools_list_request), file=stdin, flush=True)

            initialize_response_line = stdout.readline()
            initialize_response = json.loads(initialize_response_line.strip())
            assert initialize_response["id"] == 1
            assert initialize_response["result"]["serverInfo"]["name"] == "fake-stdio-mcp"

            tools_list_response_line = stdout.readline()
            tools_list_response = json.loads(tools_list_response_line.strip())
            assert tools_list_response["id"] == self.TOOLS_LIST_ID
            tools = tools_list_response["result"]["tools"]
            assert len(tools) == 1
            assert tools[0]["name"] == "fake_tool"
            assert tools[0]["inputSchema"] == {"type": "object", "properties": {}}
