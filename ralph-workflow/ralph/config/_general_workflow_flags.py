
from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class GeneralWorkflowFlags(RalphBaseModel):

    model_config = ConfigDict(frozen=True)

    checkpoint_enabled: bool = True
    unsafe_mode: bool = False


__all__ = ["GeneralWorkflowFlags"]
