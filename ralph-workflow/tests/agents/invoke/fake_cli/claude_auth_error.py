from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Authenticating session."}]},
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
                "input": {"path": "credentials.json"},
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
                        "content": [{"type": "text", "text": "credentials data"}],
                        "name": "Read",
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
