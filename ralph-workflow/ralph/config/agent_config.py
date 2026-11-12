from __future__ import annotations

import re
import shlex
from pathlib import PurePath

from pydantic import ConfigDict

from ralph.config.enums import AgentTransport, JsonParserType
from ralph.pydantic_compat import RalphBaseModel

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ENV_OPTIONS_WITH_ARGUMENT = frozenset({"-u", "--unset", "-C", "--chdir"})


def command_binary_name(cmd: str) -> str:
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        name = PurePath(token).name
        if _ENV_ASSIGNMENT.match(token):
            index += 1
        elif name == "env" or (index > 0 and token.startswith("-")):
            index += 2 if token in _ENV_OPTIONS_WITH_ARGUMENT else 1
        else:
            return name
    return ""


class AgentConfig(RalphBaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cmd: str
    output_flag: str | None = None
    yolo_flag: str | None = None
    verbose_flag: str | None = None
    json_parser: JsonParserType = JsonParserType.GENERIC
    model_flag: str | None = None
    print_flag: str | None = None
    streaming_flag: str | None = None
    session_flag: str | None = None
    display_name: str | None = None
    transport: AgentTransport | None = None
    subagent_capability: bool | None = None
    model: str | None = None

    def model_post_init(self, _context: object) -> None:
        if self.transport is not None:
            self._resolve_subagent_capability()
            return

        parser_to_transport = {
            JsonParserType.CLAUDE: AgentTransport.CLAUDE,
            JsonParserType.CODEX: AgentTransport.CODEX,
            JsonParserType.OPENCODE: AgentTransport.OPENCODE,
            JsonParserType.PI: AgentTransport.PI,
        }
        command_to_transport = {
            "claude": AgentTransport.CLAUDE_INTERACTIVE,
            "codex": AgentTransport.CODEX,
            "opencode": AgentTransport.OPENCODE,
            "nanocoder": AgentTransport.NANOCODER,
            "agy": AgentTransport.AGY,
            "pi": AgentTransport.PI,
            "kimi": AgentTransport.KIMI,
        }
        command_name = command_binary_name(self.cmd)
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


__all__ = ["AgentConfig", "command_binary_name"]
