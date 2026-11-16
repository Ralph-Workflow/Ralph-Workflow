from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Claude querying model."}]},
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "input": {"path": "config.json"}},
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
                        "content": [{"type": "text", "text": "config data"}],
                        "name": "Read",
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
