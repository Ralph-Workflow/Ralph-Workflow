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
                "text": "Pi starting inspection.",
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
                    "id": "call-pi-nz-1",
                    "name": "Read",
                    "input": {"path": "config.json"},
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
            "toolCallId": "call-pi-nz-1",
            "toolName": "Read",
            "result": {"content": [{"type": "text", "text": '{"ok": true}'}]},
            "isError": False,
        }
    ),
    flush=True,
)
sys.exit(1)
