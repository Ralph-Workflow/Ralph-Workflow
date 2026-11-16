from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {
            "type": "item.started",
            "item": {
                "id": "mcp-1",
                "type": "mcp_tool_call",
                "tool": "list_mcp_resources",
                "arguments": {"server": "ralph"},
                "status": "in_progress",
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-1",
                "type": "mcp_tool_call",
                "tool": "list_mcp_resources",
                "arguments": {"server": "ralph"},
                "status": "completed",
                "result": {"content": [{"type": "text", "text": '{"resources":[]}'}]},
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "item.started",
            "item": {
                "id": "cmd-1",
                "type": "command_execution",
                "command": "uv run pytest -q",
                "status": "in_progress",
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "cmd-1",
                "type": "command_execution",
                "command": "uv run pytest -q",
                "status": "failed",
                "exit_code": 1,
                "error": "tests failed",
            },
        }
    ),
    flush=True,
)
sys.exit(1)
