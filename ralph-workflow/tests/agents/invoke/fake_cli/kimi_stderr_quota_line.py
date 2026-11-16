from __future__ import annotations

import json
import sys

print(json.dumps({"role": "meta", "type": "system.version", "version": "2.3.0"}), flush=True)
print(
    json.dumps(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "type": "function",
                    "id": "tool_kimi_quota_01",
                    "function": {
                        "name": "Read",
                        "arguments": json.dumps({"path": "greeting.txt"}),
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
            "tool_call_id": "tool_kimi_quota_01",
            "content": "file contents",
        }
    ),
    flush=True,
)
sys.stderr.write("quota exceeded\n")
sys.stderr.flush()
sys.exit(1)
