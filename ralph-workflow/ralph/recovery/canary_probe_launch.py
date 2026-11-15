from __future__ import annotations

from contextlib import contextmanager
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from ralph.agents.invoke import InvokeOptions
from ralph.agents.invoke._command_builders import COMMAND_BUILDERS
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from ralph.agents.invoke import BuildCommandOptions
    from ralph.config.agent_config import AgentConfig

__all__ = [
    "PROBE_NONINTERACTIVE_FLAGS",
    "probe_command_builder",
    "probe_launch_override",
]

PROBE_NONINTERACTIVE_FLAGS: Final[Mapping[AgentTransport, str]] = MappingProxyType(
    {
        AgentTransport.CLAUDE_INTERACTIVE: "-p",
        AgentTransport.CLAUDE: "-p",
    }
)

_PROMPT_SEPARATOR: Final[str] = "--"


def probe_command_builder(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: BuildCommandOptions,
) -> list[str]:
    transport = (
        config.transport if isinstance(config.transport, AgentTransport) else AgentTransport.GENERIC
    )
    argv = COMMAND_BUILDERS[transport]().build(config, prompt_file, options=options)
    flag = PROBE_NONINTERACTIVE_FLAGS.get(transport)
    if flag is None or flag in argv:
        return argv
    return _insert_before_prompt(argv, flag)


def _insert_before_prompt(argv: list[str], flag: str) -> list[str]:
    index = len(argv) - 1
    if index > 0 and argv[index - 1] == _PROMPT_SEPARATOR:
        index -= 1
    return [*argv[:index], flag, *argv[index:]]


@contextmanager
def probe_launch_override() -> Iterator[None]:
    with InvokeOptions.command_builder_override(probe_command_builder):
        yield
