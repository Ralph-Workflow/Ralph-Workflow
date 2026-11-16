from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Cursor running"}]}}
    ),
    flush=True,
)
print(
    json.dumps({"type": "tool_call", "toolName": "Search", "args": {"query": "ParallelDisplay"}}),
    flush=True,
)
print(
    json.dumps({"type": "tool_result", "toolName": "Search", "result": "one result"}),
    flush=True,
)
sys.stderr.write("ralph-test-harness-auth-marker: 401 unauthorized\n")
sys.stderr.flush()
sys.exit(1)
