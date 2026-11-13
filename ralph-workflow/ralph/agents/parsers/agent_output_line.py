from __future__ import annotations

from dataclasses import dataclass, field

from .content_channel import ContentChannel

_LINE_TYPE_CHANNELS: dict[str, ContentChannel] = {
    "tool_use": ContentChannel.TOOL_CALL,
    "tool_call": ContentChannel.TOOL_CALL,
    "mcp_tool_call": ContentChannel.TOOL_CALL,
    "function_call": ContentChannel.TOOL_CALL,
    "tool_result": ContentChannel.TOOL_RESULT,
    "thinking": ContentChannel.REASONING,
    "reasoning": ContentChannel.REASONING,
    "subagent": ContentChannel.SUBAGENT,
    "user": ContentChannel.USER,
}


def channel_for_line_type(line_type: str) -> ContentChannel:
    return _LINE_TYPE_CHANNELS.get(line_type, ContentChannel.PROVIDER)


@dataclass(frozen=True, slots=True)
class AgentOutputLine:
    type: str
    content: str = ""
    raw: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    timestamp: str | None = None
    channel: ContentChannel = ContentChannel.PROVIDER

    def __post_init__(self) -> None:
        derived = channel_for_line_type(self.type)
        if self.channel is ContentChannel.PROVIDER and derived is not ContentChannel.PROVIDER:
            object.__setattr__(self, "channel", derived)


AgentOutputLine.__doc__ = "Legacy parser output line representation."
