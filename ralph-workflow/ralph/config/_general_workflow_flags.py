"""GeneralWorkflowFlags model — workflow automation flags."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class GeneralWorkflowFlags(RalphBaseModel):
    """General configuration workflow automation flags."""

    model_config = ConfigDict(frozen=True)

    checkpoint_enabled: bool = True


__all__ = ["GeneralWorkflowFlags"]
