from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

ALL_RESOLVERS_WAITING_MESSAGE = "all resolver agents are on cooldown"
NO_RESOLVER_AGENTS_CONFIGURED = "no resolver agents are configured"
NO_RESOLVER_CAN_RUN_ANOTHER_ROUND = "no resolver agent can run another round this stop"

_RESOLVER_WAIT_TEXT = re.compile(
    r"all (?:\d+ )?resolver agents? (?:is|are) on cooldown"
    rf"|{re.escape(NO_RESOLVER_AGENTS_CONFIGURED)}"
    rf"|{re.escape(NO_RESOLVER_CAN_RUN_ANOTHER_ROUND)}"
)


def resolver_cooldown_summary(agent_cooldowns: Sequence[tuple[str, str | None]]) -> str:
    if not agent_cooldowns:
        return NO_RESOLVER_AGENTS_CONFIGURED
    details = "; ".join(
        f"{agent}: {cooldown}" if cooldown is not None else f"{agent}: available, out of rounds"
        for agent, cooldown in agent_cooldowns
    )
    if any(cooldown is None for _agent, cooldown in agent_cooldowns):
        return f"{NO_RESOLVER_CAN_RUN_ANOTHER_ROUND} ({details})"
    count = len(agent_cooldowns)
    noun = "resolver agent is" if count == 1 else "resolver agents are"
    return f"all {count} {noun} on cooldown ({details})"


def resolvers_on_cooldown_reason(paths: Sequence[str], command: str) -> str:
    return (
        f"{ALL_RESOLVERS_WAITING_MESSAGE}; conflict on {', '.join(paths)}; resume point: {command}"
    )


def names_resolver_cooldown(text: str) -> bool:
    return _RESOLVER_WAIT_TEXT.search(text) is not None


__all__ = [
    "ALL_RESOLVERS_WAITING_MESSAGE",
    "NO_RESOLVER_AGENTS_CONFIGURED",
    "NO_RESOLVER_CAN_RUN_ANOTHER_ROUND",
    "names_resolver_cooldown",
    "resolver_cooldown_summary",
    "resolvers_on_cooldown_reason",
]
