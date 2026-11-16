from __future__ import annotations

import json
import os
import signal
import sys
import threading

print(json.dumps({"role": "meta", "type": "system.version", "version": "2.3.0"}), flush=True)
print(
    json.dumps(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "type": "function",
                    "id": "tool_kimi_hang_01",
                    "function": {
                        "name": "Write",
                        "arguments": json.dumps({"path": "output.txt", "content": "partial"}),
                    },
                }
            ],
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "role": "tool",
            "tool_call_id": "tool_kimi_hang_01",
            "content": "Wrote 7 bytes to output.txt",
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
