from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Claude Interactive querying model."},
                    {
                        "type": "tool_use",
                        "id": "toolu_read",
                        "name": "Read",
                        "input": {"path": "config.json"},
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
                        "content": [{"type": "text", "text": "config data"}],
                    }
                ]
            },
        }
    ),
    flush=True,
)
sys.stderr.write("quota exceeded\n")
sys.stderr.flush()
sys.exit(1)
