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
                "text": "Pi checking quota limits.",
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
                    "id": "call-pi-quota-1",
                    "name": "Read",
                    "input": {"path": "limits.json"},
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
            "toolCallId": "call-pi-quota-1",
            "toolName": "Read",
            "result": {"content": [{"type": "text", "text": '{"limit": 100}'}]},
            "isError": False,
        }
    ),
    flush=True,
)
sys.stderr.write("quota exceeded\n")
sys.stderr.flush()
sys.exit(1)
