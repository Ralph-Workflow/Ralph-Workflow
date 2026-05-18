from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class SkillsMcp(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)
