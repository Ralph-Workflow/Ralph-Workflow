from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class _FrozenPolicyModel(RalphBaseModel):
    model_config = ConfigDict(frozen=True)
