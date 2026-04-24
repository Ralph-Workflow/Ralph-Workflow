"""Shared lifecycle-line filter used by all display intake paths."""

from __future__ import annotations

import re
from typing import Final

# Bare lifecycle token values — exact matches after stripping a provider prefix.
# These carry no user payload and must never surface as display content.
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
    }
)

# Matches "system (status=<word>)" lifecycle lines.
SYSTEM_STATUS_RE: Final[re.Pattern[str]] = re.compile(r"^system \(status=\w+\)$")

# Matches an optional "<provider>/<model>: " prefix on transcript lines.
PROVIDER_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]*/[^:]+: ")


def is_bare_lifecycle(line: str) -> bool:
    """Return True when *line* is a bare lifecycle token with no user payload.

    Strips an optional "<provider>/<model>: " prefix before checking.
    Only exact token matches are suppressed; longer content strings pass through.
    Lines containing "✗:" are never suppressed (they are real error lines).
    """
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
