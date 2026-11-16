from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Claude Interactive starts operation."},
                    {
                        "type": "tool_use",
                        "id": "toolu_read",
                        "name": "Read",
                        "input": {"path": "nonexistent.txt"},
                    },
                ]
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read",
                        "content": [{"type": "text", "text": "file not found"}],
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
sys.exit(1)
