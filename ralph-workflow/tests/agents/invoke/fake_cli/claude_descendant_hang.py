from __future__ import annotations

import json
import os
import signal
import sys
import threading

print(
    json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Claude starts operation."}]},
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "Read",
                "input": {"path": "nonexistent.txt"},
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [{"type": "text", "text": "file not found"}],
                        "name": "Read",
                        "is_error": True,
                    }
                ]
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
