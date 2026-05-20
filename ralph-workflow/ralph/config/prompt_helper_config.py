"""Prompt helper configuration."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class PromptHelperConfig(RalphBaseModel):
    """Configuration for the prompt helper feature."""

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(default="prompt-helper-agent", min_length=1)


__all__ = ["PromptHelperConfig"]
