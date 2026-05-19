"""Private base model for frozen policy models."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class _FrozenPolicyModel(RalphBaseModel):
    """Private base for frozen policy models."""

    model_config = ConfigDict(frozen=True)
