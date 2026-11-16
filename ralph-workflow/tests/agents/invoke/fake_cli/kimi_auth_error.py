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
                    "id": "tool_kimi_auth_01",
                    "function": {
                        "name": "Write",
                        "arguments": json.dumps({"path": "auth_test.txt", "content": "auth"}),
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
            "tool_call_id": "tool_kimi_auth_01",
            "content": "Wrote 4 bytes to auth_test.txt",
        }
    ),
    flush=True,
)
sys.stderr.write("ralph-test-harness-auth-marker: 401 unauthorized\n")
sys.stderr.flush()
sys.exit(1)
