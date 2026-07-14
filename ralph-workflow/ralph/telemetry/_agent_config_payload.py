"""Privacy-bounded snapshot of the resolved ``[agents.*]`` configuration.

Reduces the loaded agent table to metadata only: closed-vocabulary transports,
the model identifier, and booleans recording WHICH flags are set. The
user-authored agent names (dict keys), the raw ``cmd`` string, and every flag
VALUE are dropped here and never reach the transport layer — ``cmd`` can embed
absolute paths, wrapper scripts, or env prefixes, so it is reduced to a known
binary name or ``custom``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.config.agent_transport import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.config.agent_config import AgentConfig

# Static dispatch table (all-static keys): transport value -> coarse family.
AGENT_FAMILY_BY_TRANSPORT: dict[str, str] = {
    AgentTransport.CLAUDE.value: "claude",
    AgentTransport.CLAUDE_INTERACTIVE.value: "claude_interactive",
    AgentTransport.CODEX.value: "codex",
    AgentTransport.OPENCODE.value: "opencode",
    AgentTransport.NANOCODER.value: "nanocoder",
    AgentTransport.AGY.value: "agy",
    AgentTransport.PI.value: "pi",
    AgentTransport.CURSOR.value: "cursor",
    AgentTransport.GENERIC.value: "custom",
}

# Only these binary names survive the ``cmd`` reduction. Anything else — a
# wrapper script, an absolute path, a user-authored launcher — collapses to
# ``custom`` so no filesystem or tooling identity leaves the process.
_KNOWN_AGENT_BINARIES: frozenset[str] = frozenset(
    {"claude", "codex", "opencode", "nanocoder", "agy", "pi", "cursor"}
)

# Caps the forwarded entry count so a pathological config cannot inflate the
# event payload. The true size is still reported via ``agent_count``.
AGENT_CONFIG_MAX_ENTRIES: int = 32

_CUSTOM: str = "custom"

# ``model`` is forwarded verbatim because a model IDENTIFIER is a product
# identifier, not PII. But the field is free text: local-model and proxy
# workflows (ollama, llama.cpp, vLLM, LiteLLM) legitimately put a filesystem
# path or a credentialed endpoint URL there — e.g.
# ``/home/jane/acme/secret-ft.gguf`` or ``http://user:pw@llm.internal/v1/m``.
# Those are exactly the paths and secrets the metadata-only contract forbids,
# and the ``before_send`` prefix scrubber cannot neutralize them (it only
# rewrites the home/cwd/argv PREFIX, leaving the rest of the path intact).
#
# So a model string is forwarded only when it LOOKS like an identifier: dotted /
# hyphenated segments, at most two ``/`` separators (``vendor/family/name``),
# and no scheme, credential, whitespace, or backslash. Anything else — every
# absolute path and every URL — collapses to ``custom``.
_MODEL_ID_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]*(?:/[A-Za-z0-9._:+-]+){0,2}$")
_MODEL_ID_MAX_LEN: int = 96


def _binary_name(cmd: str) -> str:
    """Reduce a ``cmd`` string to a known agent binary name, else ``custom``."""
    parts = cmd.split()
    if not parts:
        return _CUSTOM
    basename = parts[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return basename if basename in _KNOWN_AGENT_BINARIES else _CUSTOM


def _safe_model(model: str | None) -> str | None:
    """Forward a model IDENTIFIER; collapse a path/URL/free-text value to ``custom``.

    Returns ``None`` when no model is configured, so ``model is None`` stays
    distinguishable from "a model was set but it was not an identifier".
    """
    if model is None:
        return None
    candidate = model.strip()
    if not candidate or len(candidate) > _MODEL_ID_MAX_LEN:
        return _CUSTOM
    if _MODEL_ID_RE.fullmatch(candidate) is None:
        return _CUSTOM
    return candidate


def _transport_value(agent: AgentConfig) -> str:
    """Return the agent's transport as its raw enum value, or ``generic``."""
    transport = agent.transport
    if isinstance(transport, AgentTransport):
        return transport.value
    return AgentTransport.GENERIC.value


def _flags_present(agent: AgentConfig) -> dict[str, bool]:
    """Record WHICH invocation flags are configured, never their values."""
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
    """Derive a stable, name-free key: the family, suffixed on collision."""
    if family not in taken:
        return family
    index = 2
    while f"{family}_{index}" in taken:
        index += 1
    return f"{family}_{index}"


def build_agent_config_payload(agents: Mapping[str, AgentConfig]) -> dict[str, object]:
    """Build the metadata-only ``agent_config`` context payload.

    The mapping's keys are user-authored agent names and are deliberately
    discarded: each entry is re-keyed by its transport-derived family, with a
    numeric suffix on collision.
    """
    entries: dict[str, object] = {}
    families: set[str] = set()
    total = len(agents)
    configured = list(agents.values())

    # Families are summarized over EVERY agent, not just the forwarded ones, so
    # the tag stays accurate for a config larger than the entry cap.
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
            "can_commit": agent.can_commit,
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
