"""Load-time warnings for conflict-resolution agent chains."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

_MIN_CONFLICT_FALLBACK_CHAIN_LENGTH = 2
_CONFLICT_RESOLUTION_DRAIN = "rebase_conflict_resolution"


def warn_if_conflict_resolution_chain_has_no_fallback(bundle: PolicyBundle) -> None:
    """Warn at load when rebase_conflict_resolution has no fallback agent."""
    drain_binding = bundle.agents.agent_drains.get(_CONFLICT_RESOLUTION_DRAIN)
    if drain_binding is None:
        return
    chain_config = bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None:
        return
    if len(chain_config.agents) >= _MIN_CONFLICT_FALLBACK_CHAIN_LENGTH:
        return
    logger.warning(
        "rebase_conflict_resolution: drain is bound to a one-agent chain '{}'; "
        "there is no fallback candidate if this resolver fails",
        drain_binding.chain,
    )
