from __future__ import annotations

import json
import sys


def main() -> None:
    initialized = False
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "fake-stdio-mcp", "version": "0.1.0"},
                    "capabilities": {},
                },
            }
            print(json.dumps(response), flush=True)
        elif method == "notifications/initialized":
            initialized = True
        elif method == "tools/list" and initialized:
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "fake_tool",
                            "description": "A fake tool for testing",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            }
            print(json.dumps(response), flush=True)
        elif method == "tools/call" and initialized:
            params = request.get("params", {})
            tool_name = params.get("name")
            if tool_name == "fake_tool":
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": "fake-result"}],
                        "isError": False,
                    },
                }
                print(json.dumps(response), flush=True)
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
                }
                print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
