from __future__ import annotations

import re
from typing import Final

BARE_LIFECYCLE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "message_delta",
        "message_start",
        "message_stop",
        "content_block_start",
        "content_block_stop",
        "thinking",
        "user",
        "assistant",
        "turn.started",
        "turn.completed",
        "thread.started",
        "response.completed",
        "done",
        "complete",
        "stop",
        "message_end",
        "message_started",
        "heartbeat",
        "ping",
        "ready",
        "begin",
        "start",
        "step_start",
        "step_finish",
        "finish",
        "end",
    }
)

SYSTEM_STATUS_RE: Final[re.Pattern[str]] = re.compile(r"^system \(status=\w+\)$")

PROVIDER_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]*/[^:]+: ")


def is_bare_lifecycle(line: str) -> bool:
    if " ✗: " in line or line.endswith(" ✗:"):
        return False
    remainder = PROVIDER_PREFIX_RE.sub("", line, count=1)
    return remainder in BARE_LIFECYCLE_TOKENS or bool(SYSTEM_STATUS_RE.match(remainder))


__all__ = [
    "BARE_LIFECYCLE_TOKENS",
    "PROVIDER_PREFIX_RE",
    "SYSTEM_STATUS_RE",
    "is_bare_lifecycle",
]
