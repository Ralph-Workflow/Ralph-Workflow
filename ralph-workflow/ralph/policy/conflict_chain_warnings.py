"""Load-time warnings for conflict-resolution agent chains."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

_MIN_CONFLICT_FALLBACK_CHAIN_LENGTH = 2
_CONFLICT_RESOLUTION_DRAIN = "rebase_conflict_resolution"
#: Distinct chain names warned about per process; far above any real config.
_MAX_WARNED_CHAINS = 32


def _warn_chain_has_no_fallback_uncached(chain: str) -> None:
    logger.warning(
        "rebase_conflict_resolution: drain is bound to a one-agent chain '{}'; "
        "there is no fallback candidate if this resolver fails",
        chain,
    )


#: Memoised so the warning is emitted once per process for each chain name.
_warn_chain_has_no_fallback = lru_cache(maxsize=_MAX_WARNED_CHAINS)(
    _warn_chain_has_no_fallback_uncached
)


def warn_if_conflict_resolution_chain_has_no_fallback(bundle: PolicyBundle) -> None:
    """Warn at load when rebase_conflict_resolution has no fallback agent.

    A long-lived orchestrator reloads policy for every phase invocation, so
    the warning is emitted once per process for each chain name instead of on
    every load.
    """
    drain_binding = bundle.agents.agent_drains.get(_CONFLICT_RESOLUTION_DRAIN)
    if drain_binding is None:
        return
    chain_config = bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None:
        return
    if len(chain_config.agents) >= _MIN_CONFLICT_FALLBACK_CHAIN_LENGTH:
        return
    _warn_chain_has_no_fallback(drain_binding.chain)
