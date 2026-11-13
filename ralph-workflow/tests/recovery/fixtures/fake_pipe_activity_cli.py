from __future__ import annotations

import json
import sys

_TOOL_USE_EVENT = json.dumps(
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {
            "type": "tool_use",
            "id": "toolu_fake_1",
            "name": "Bash",
            "input": {"command": "ls"},
        },
    }
)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "text"
    if mode == "tool_use":
        sys.stdout.write(f"{_TOOL_USE_EVENT}\n")
    else:
        sys.stdout.write("fake-pipe-activity-cli: plain text reply, no structured event\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
