"""Critical-files section of a plan artifact.

Lists the primary files the plan will change plus any reference files
that provide context but are not themselves modified. Primary files are
required; reference files are optional.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.mcp.artifacts.plan._critical_primary_file import CriticalPrimaryFile
from ralph.mcp.artifacts.plan._reference_file import ReferenceFile
from ralph.pydantic_compat import RalphBaseModel


class CriticalFiles(RalphBaseModel):
    """Primary and reference files that define the plan's surface area."""

    model_config = ConfigDict(extra="forbid")

    primary_files: list[CriticalPrimaryFile] = Field(..., min_length=1, max_length=200)
    reference_files: list[ReferenceFile] = Field(default_factory=list, max_length=200)
