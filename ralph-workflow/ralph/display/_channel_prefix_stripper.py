
from __future__ import annotations

import re

_PARSER_CHANNEL_PREFIXES: tuple[str, ...] = (
    "text: ",
    "thinking: ",
    "tool_use: ",
    "tool_result: ",
)

_PARSER_CHANNEL_PREFIXES_SPACELESS: tuple[str, ...] = (
    "text:",
    "thinking:",
    "tool_use:",
    "tool_result:",
)


_LIVE_BADGE_PREFIX = re.compile(
    r"^(?:[\u25d0\u25d1\u25d2\u25d3]\s+RUN\s+(?:\d{2}:\d{2}:\d{2}\s+)?)"
)
_LIVE_IDENTITY_CHANNEL_PREFIX = re.compile(r"^\S+\s+(?=(?:text|thinking|tool_use|tool_result):)")


def strip_parser_channel_prefix(content: str) -> str:
    content = _LIVE_BADGE_PREFIX.sub("", content)
    content = _LIVE_IDENTITY_CHANNEL_PREFIX.sub("", content)
    for prefix in _PARSER_CHANNEL_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix) :]
    for prefix in _PARSER_CHANNEL_PREFIXES_SPACELESS:
        if content.startswith(prefix):
            remainder = content[len(prefix) :]
            if remainder and not remainder[0].isspace():
                return remainder
    return content
