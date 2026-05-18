from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class EditArea(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(default_factory=list)
    directories: list[str] = Field(default_factory=list)
