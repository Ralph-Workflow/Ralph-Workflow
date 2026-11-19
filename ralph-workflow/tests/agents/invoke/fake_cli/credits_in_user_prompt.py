from __future__ import annotations

import json

print(
    json.dumps(
        {
            "type": "message_start",
            "message": {
                "role": "user",
                "content": "the previous run ended with credits exhausted",
            },
            "error": "credits exhausted",
        }
    ),
    flush=True,
)
