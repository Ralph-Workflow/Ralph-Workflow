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
                    "id": "tool_kimi_nz_01",
                    "function": {
                        "name": "Write",
                        "arguments": json.dumps({"path": "output.txt", "content": "partial"}),
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
            "tool_call_id": "tool_kimi_nz_01",
            "content": "Wrote 7 bytes to output.txt",
        }
    ),
    flush=True,
)
sys.exit(1)
