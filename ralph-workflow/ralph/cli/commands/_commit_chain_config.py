from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import GeneralConfig
    from ralph.policy.models import AgentsPolicy


@dataclass(frozen=True)
class CommitChainConfig:
    """Configuration bundle for the commit message chain invocation."""

    registry: AgentRegistry
    agents: list[str]
    verbose: bool
    agents_policy: AgentsPolicy
    general_config: GeneralConfig | None = None
