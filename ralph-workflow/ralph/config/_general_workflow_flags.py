"""GeneralWorkflowFlags model — workflow automation flags."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class GeneralWorkflowFlags(RalphBaseModel):
    """General configuration workflow automation flags."""

    model_config = ConfigDict(frozen=True)

    checkpoint_enabled: bool = True
    unsafe_mode: bool = False
    """When ``True`` Ralph merges its MCP server into the agent's existing MCP
    configuration instead of replacing it, giving the agent access to Ralph
    tools alongside whatever MCP servers it already had. When ``False`` (the
    default) Ralph overwrites the agent's MCP config with a Ralph-only server
    set, matching the strict-authority contract used in unattended runs.
    """


__all__ = ["GeneralWorkflowFlags"]
