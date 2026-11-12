from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.config.agent_transport import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.config.agent_config import AgentConfig

AGENT_FAMILY_BY_TRANSPORT: dict[str, str] = {
    AgentTransport.CLAUDE.value: "claude",
    AgentTransport.CLAUDE_INTERACTIVE.value: "claude_interactive",
    AgentTransport.CODEX.value: "codex",
    AgentTransport.OPENCODE.value: "opencode",
    AgentTransport.NANOCODER.value: "nanocoder",
    AgentTransport.AGY.value: "agy",
    AgentTransport.PI.value: "pi",
    AgentTransport.CURSOR.value: "cursor",
    AgentTransport.KIMI.value: "kimi",
    AgentTransport.GENERIC.value: "custom",
}

_KNOWN_AGENT_BINARIES: frozenset[str] = frozenset(
    {"claude", "codex", "opencode", "nanocoder", "agy", "pi", "cursor", "kimi"}
)

AGENT_CONFIG_MAX_ENTRIES: int = 32

_CUSTOM: str = "custom"

_MODEL_ID_RE: re.Pattern[str] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:+-]*(?:/[A-Za-z0-9._:+-]+){0,2}$"
)
_MODEL_ID_MAX_LEN: int = 96


def _binary_name(cmd: str) -> str:
    parts = cmd.split()
    if not parts:
        return _CUSTOM
    basename = parts[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return basename if basename in _KNOWN_AGENT_BINARIES else _CUSTOM


def _safe_model(model: str | None) -> str | None:
    if model is None:
        return None
    candidate = model.strip()
    if not candidate or len(candidate) > _MODEL_ID_MAX_LEN:
        return _CUSTOM
    if _MODEL_ID_RE.fullmatch(candidate) is None:
        return _CUSTOM
    return candidate


def _transport_value(agent: AgentConfig) -> str:
    transport = agent.transport
    if isinstance(transport, AgentTransport):
        return transport.value
    return AgentTransport.GENERIC.value


def _flags_present(agent: AgentConfig) -> dict[str, bool]:
    return {
        "output_flag": agent.output_flag is not None,
        "yolo_flag": agent.yolo_flag is not None,
        "verbose_flag": agent.verbose_flag is not None,
        "model_flag": agent.model_flag is not None,
        "print_flag": agent.print_flag is not None,
        "streaming_flag": agent.streaming_flag is not None,
        "session_flag": agent.session_flag is not None,
    }


def _unique_slug(family: str, taken: Mapping[str, object]) -> str:
    if family not in taken:
        return family
    index = 2
    while f"{family}_{index}" in taken:
        index += 1
    return f"{family}_{index}"


def build_agent_config_payload(agents: Mapping[str, AgentConfig]) -> dict[str, object]:
    entries: dict[str, object] = {}
    families: set[str] = set()
    total = len(agents)
    configured = list(agents.values())

    for agent in configured:
        families.add(AGENT_FAMILY_BY_TRANSPORT.get(_transport_value(agent), _CUSTOM))

    for agent in configured[:AGENT_CONFIG_MAX_ENTRIES]:
        transport_value = _transport_value(agent)
        family = AGENT_FAMILY_BY_TRANSPORT.get(transport_value, _CUSTOM)
        entries[_unique_slug(family, entries)] = {
            "transport": (
                transport_value
                if transport_value in AGENT_FAMILY_BY_TRANSPORT
                else AgentTransport.GENERIC.value
            ),
            "agent_family": family,
            "binary": _binary_name(agent.cmd),
            "model": _safe_model(agent.model),
            "json_parser": agent.json_parser.value,
            "subagent_capability": agent.subagent_capability,
            "flags": _flags_present(agent),
        }

    return {
        "agent_count": total,
        "truncated": total > AGENT_CONFIG_MAX_ENTRIES,
        "agent_families": ",".join(sorted(families)),
        "agents": entries,
    }


__all__ = [
    "AGENT_CONFIG_MAX_ENTRIES",
    "AGENT_FAMILY_BY_TRANSPORT",
    "build_agent_config_payload",
]
