from __future__ import annotations

import json
import os
import signal

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
print(
    json.dumps(
        {
            "type": "error",
            "error": {"type": "process_error", "message": "fatal error during execution"},
        }
    ),
    flush=True,
)
os.kill(os.getpid(), signal.SIGKILL)
