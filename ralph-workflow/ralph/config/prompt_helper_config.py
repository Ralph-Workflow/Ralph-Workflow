"""Prompt helper configuration."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class PromptHelperConfig(RalphBaseModel):
    """Configuration for the prompt helper feature."""

    model_config = ConfigDict(extra="forbid")

    agent: str | None = None


__all__ = ["PromptHelperConfig"]
