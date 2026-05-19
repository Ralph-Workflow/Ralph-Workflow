from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.mcp.artifacts.plan._critical_primary_file import CriticalPrimaryFile
from ralph.mcp.artifacts.plan._reference_file import ReferenceFile
from ralph.pydantic_compat import RalphBaseModel


class CriticalFiles(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_files: list[CriticalPrimaryFile] = Field(..., min_length=1)
    reference_files: list[ReferenceFile] = Field(default_factory=list)
