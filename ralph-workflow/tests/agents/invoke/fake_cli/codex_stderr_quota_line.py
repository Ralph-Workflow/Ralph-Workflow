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
sys.stderr.write("quota exceeded\n")
sys.stderr.flush()
sys.exit(1)
