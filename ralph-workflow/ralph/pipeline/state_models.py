"""Frozen sub-models used by the immutable pipeline state model."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.commit_state import CommitState
from ralph.pipeline.fallover_record import FalloverRecord
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.run_metrics import RunMetrics
from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class _FrozenPipelineStateModel(RalphBaseModel):
    """Private base for frozen pipeline state models."""

    model_config = _FROZEN


__all__ = [
    "AgentChainState",
    "CommitState",
    "FalloverRecord",
    "RebaseState",
    "RunMetrics",
    "_FrozenPipelineStateModel",
]
