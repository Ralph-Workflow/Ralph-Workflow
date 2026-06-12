"""Agent configuration model definitions."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.config.enums import AgentTransport, JsonParserType
from ralph.pydantic_compat import RalphBaseModel


class AgentConfig(RalphBaseModel):
    """Configuration for a single AI agent.

    Attributes:
        cmd: Base command to run the agent.
        output_flag: Optional output format flag for streaming JSON.
        yolo_flag: Optional autonomous/non-interactive flag string.
        verbose_flag: Flag for verbose output.
        can_commit: Whether the agent can run git commit.
        json_parser: Which JSON parser to use for agent output.
        model_flag: Optional model/provider flag.
        print_flag: Optional print flag for non-interactive output mode.
        streaming_flag: Optional streaming flag for partial JSON messages.
        session_flag: Optional session continuation flag template.
        display_name: Human-readable display name for UI/UX.
        transport: Invocation/MCP transport type for the agent runtime.
        subagent_capability: Whether the agent runtime exposes a usable
            sub-agent / task tooling that can dispatch parallel work. When
            ``None`` (the default), it is inferred from the resolved
            ``transport``: Claude / Claude-interactive runs default to
            ``True``; every other transport defaults to ``None`` (no
            inference, the agent decides at runtime). The bundled
            ``ralph-workflow.toml`` ships with ``[agents.claude]
            subagent_capability = true`` so new installs and partial
            overrides both inherit the sub-agent-enabled default.
    """

    model_config = ConfigDict(frozen=True)

    cmd: str
    output_flag: str | None = None
    yolo_flag: str | None = None
    verbose_flag: str | None = None
    can_commit: bool = False
    json_parser: JsonParserType = JsonParserType.GENERIC
    model_flag: str | None = None
    print_flag: str | None = None
    streaming_flag: str | None = None
    session_flag: str | None = None
    display_name: str | None = None
    transport: AgentTransport | None = None
    subagent_capability: bool | None = None

    def model_post_init(self, _context: object) -> None:
        if self.transport is not None:
            self._resolve_subagent_capability()
            return

        parser_to_transport = {
            JsonParserType.CLAUDE: AgentTransport.CLAUDE,
            JsonParserType.CODEX: AgentTransport.CODEX,
            JsonParserType.OPENCODE: AgentTransport.OPENCODE,
        }
        command_to_transport = {
            "claude": AgentTransport.CLAUDE_INTERACTIVE,
            "codex": AgentTransport.CODEX,
            "opencode": AgentTransport.OPENCODE,
            "nanocoder": AgentTransport.NANOCODER,
            "agy": AgentTransport.AGY,
        }
        command_name = self.cmd.split()[0] if self.cmd else ""
        inferred_transport = parser_to_transport.get(
            self.json_parser,
            command_to_transport.get(command_name, AgentTransport.GENERIC),
        )
        object.__setattr__(self, "transport", inferred_transport)
        self._resolve_subagent_capability()

    def _resolve_subagent_capability(self) -> None:
        if self.subagent_capability is not None:
            return
        if self.transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
            object.__setattr__(self, "subagent_capability", True)


__all__ = ["AgentConfig"]
