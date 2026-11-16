from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Authenticating session."},
                    {
                        "type": "tool_use",
                        "id": "toolu_read",
                        "name": "Read",
                        "input": {"path": "credentials.json"},
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
                        "content": [{"type": "text", "text": "credentials data"}],
                    }
                ]
            },
        }
    ),
    flush=True,
)
sys.stderr.write("ralph-test-harness-auth-marker: 401 unauthorized\n")
sys.stderr.flush()
sys.exit(1)
