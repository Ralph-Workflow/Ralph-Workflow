from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel
from ralph.skills._content import BASELINE_SKILL_NAMES


class SkillsMcp(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(..., min_length=1)
    mcps: list[str] = Field(default_factory=list)

    @field_validator("skills")
    @classmethod
    def validate_skill_names(cls, skills: list[str]) -> list[str]:
        baseline_names = set(BASELINE_SKILL_NAMES)
        unknown = [name for name in skills if name not in baseline_names]
        if unknown:
            msg = f"unknown skill names: {unknown}"
            raise ValueError(msg)

        deduped: list[str] = []
        seen: set[str] = set()
        for name in skills:
            stripped = name.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            deduped.append(stripped)
        if not deduped:
            msg = "skills must contain at least one shipped skill name"
            raise ValueError(msg)
        return deduped

    @field_validator("mcps")
    @classmethod
    def normalize_mcp_names(cls, mcps: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for name in mcps:
            stripped = name.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            deduped.append(stripped)
        return deduped
