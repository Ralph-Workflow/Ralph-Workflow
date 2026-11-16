from __future__ import annotations

import json
import os
import signal
import sys
import threading

_SESSION = "s_0001"

print(
    json.dumps(
        {
            "type": "step_start",
            "timestamp": 1785000001000,
            "sessionID": _SESSION,
            "part": {
                "id": "p_0001",
                "messageID": "m_0001",
                "sessionID": _SESSION,
                "type": "step-start",
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1785000002000,
            "sessionID": _SESSION,
            "part": {
                "type": "tool",
                "tool": "read",
                "callID": "c_0001",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "/workspace/hello.txt"},
                    "output": "hello",
                    "time": {"start": 1786032889766, "end": 1786032889770},
                },
                "id": "p_0002",
                "sessionID": _SESSION,
                "messageID": "m_0001",
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "step_finish",
            "timestamp": 1785000003000,
            "sessionID": _SESSION,
            "part": {
                "id": "p_0003",
                "reason": "tool-calls",
                "messageID": "m_0001",
                "sessionID": _SESSION,
                "type": "step-finish",
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

print(
    json.dumps(
        {
            "type": "text",
            "timestamp": 1785000004000,
            "sessionID": _SESSION,
            "part": {
                "id": "p_0004",
                "messageID": "m_0002",
                "sessionID": _SESSION,
                "type": "text",
                "text": "agent parent completed",
            },
        }
    ),
    flush=True,
)
if "--reap-descendant" in sys.argv:
    os.kill(child_pid, signal.SIGKILL)
    os.waitpid(child_pid, 0)
threading.Event().wait(0.1)
