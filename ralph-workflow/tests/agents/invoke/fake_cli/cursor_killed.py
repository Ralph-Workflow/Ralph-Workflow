from __future__ import annotations

import json
import os
import signal

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
os.kill(os.getpid(), signal.SIGKILL)
