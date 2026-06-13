from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel


class SkillsMcp(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(..., min_length=1, max_length=100)
    mcps: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("skills")
    @classmethod
    def normalize_skill_names(cls, skills: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for name in skills:
            stripped = name.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            deduped.append(stripped)
        if not deduped:
            msg = "skills must contain at least one non-empty skill name"
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
