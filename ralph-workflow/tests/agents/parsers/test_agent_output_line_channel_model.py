from __future__ import annotations

from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.parsers.content_channel import ContentChannel


class TestAgentOutputLineChannelModel:
    def test_default_channel_is_provider(self) -> None:
        assert AgentOutputLine(type="text").channel is ContentChannel.PROVIDER

    def test_channel_follows_emitted_tool_call_type(self) -> None:
        assert AgentOutputLine(type="tool_use").channel is ContentChannel.TOOL_CALL

    def test_channel_follows_emitted_tool_result_type(self) -> None:
        assert AgentOutputLine(type="tool_result").channel is ContentChannel.TOOL_RESULT

    def test_channel_follows_emitted_thinking_type(self) -> None:
        assert AgentOutputLine(type="thinking").channel is ContentChannel.REASONING

    def test_channel_follows_emitted_subagent_type(self) -> None:
        assert AgentOutputLine(type="subagent").channel is ContentChannel.SUBAGENT

    def test_channel_follows_emitted_user_type(self) -> None:
        assert AgentOutputLine(type="user").channel is ContentChannel.USER

    def test_explicit_channel_is_preserved(self) -> None:
        line = AgentOutputLine(type="error", channel=ContentChannel.TOOL_RESULT)
        assert line.channel is ContentChannel.TOOL_RESULT
