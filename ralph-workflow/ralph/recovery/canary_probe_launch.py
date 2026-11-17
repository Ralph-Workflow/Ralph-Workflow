from __future__ import annotations

import shlex
from contextlib import contextmanager
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from ralph.agents.invoke import InvokeOptions
from ralph.agents.invoke._command_builders import (
    COMMAND_BUILDERS,
    HEADLESS_CLAUDE_PRINT_FLAGS,
    command_already_enables_print_mode,
)
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from ralph.agents.invoke import BuildCommandOptions
    from ralph.config.agent_config import AgentConfig

__all__ = [
    "HEADLESS_CLAUDE_PRINT_FLAGS",
    "PROBE_NONINTERACTIVE_FLAGS",
    "ProbeTransportNotSafeError",
    "probe_command_builder",
    "probe_launch_override",
]

PROBE_NONINTERACTIVE_FLAGS: Final[Mapping[AgentTransport, str]] = MappingProxyType(
    {
        AgentTransport.CLAUDE_INTERACTIVE: "-p",
    }
)

PROBE_UNSAFE_TRANSPORTS: Final[frozenset[AgentTransport]] = frozenset(
    {
        AgentTransport.NANOCODER,
    }
)

_PROMPT_SEPARATOR: Final[str] = "--"


class ProbeTransportNotSafeError(ValueError):
    pass


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
    if flag is not None and flag not in argv:
        argv = _insert_before_prompt(argv, flag)
    if transport in PROBE_UNSAFE_TRANSPORTS:
        raise ProbeTransportNotSafeError(
            f"transport {transport.value} cannot run a canary probe without an interactive TUI"
        )
    if not _carries_headless_selection(config, argv):
        raise ProbeTransportNotSafeError(
            "the canary probe argv carries no headless selection "
            f"(transport {transport.value}, argv {argv!r})"
        )
    return argv


def _carries_headless_selection(config: AgentConfig, argv: list[str]) -> bool:
    if command_already_enables_print_mode(argv):
        return True
    print_flag = config.print_flag
    if print_flag is None:
        return False
    return any(token in argv for token in shlex.split(print_flag))


def _insert_before_prompt(argv: list[str], flag: str) -> list[str]:
    index = len(argv) - 1
    if index > 0 and argv[index - 1] == _PROMPT_SEPARATOR:
        index -= 1
    return [*argv[:index], flag, *argv[index:]]


@contextmanager
def probe_launch_override() -> Iterator[None]:
    with InvokeOptions.command_builder_override(probe_command_builder):
        yield
