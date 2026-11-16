from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_end",
                "contentIndex": 0,
                "text": "Pi validating credentials.",
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "toolcall_end",
                "contentIndex": 1,
                "toolCall": {
                    "id": "call-pi-auth-1",
                    "name": "Read",
                    "input": {"path": "credentials.json"},
                },
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "tool_execution_end",
            "toolCallId": "call-pi-auth-1",
            "toolName": "Read",
            "result": {"content": [{"type": "text", "text": "{}"}]},
            "isError": False,
        }
    ),
    flush=True,
)
sys.stderr.write("ralph-test-harness-auth-marker: 401 unauthorized\n")
sys.stderr.flush()
sys.exit(1)
