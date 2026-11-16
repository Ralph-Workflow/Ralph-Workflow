from __future__ import annotations

import json
import os
import signal
import sys
import threading

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


def _ignore_sigterm(signum: int, frame: object) -> None:
    del signum, frame


child_pid = os.fork()
if child_pid == 0:
    os.close(0)
    signal.signal(signal.SIGTERM, _ignore_sigterm)
    while True:
        signal.pause()

print("agent parent completed", flush=True)
if "--reap-descendant" in sys.argv:
    os.kill(child_pid, signal.SIGKILL)
    os.waitpid(child_pid, 0)
threading.Event().wait(0.1)
