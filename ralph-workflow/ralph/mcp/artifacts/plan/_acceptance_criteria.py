"""Acceptance-criteria sub-section for the plan design schema."""

from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel


class AcceptanceCriterion(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, pattern=r"^[A-Z]+-\d{2,}$")
    description: str = Field(..., min_length=1, max_length=1000)
    verification_step: str | None = None
    evidence_path: str | None = None

    @field_validator("id")
    @classmethod
    def _strip_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "acceptance_criterion.id must not be empty"
            raise ValueError(msg)
        return stripped

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "acceptance_criterion.description must not be empty"
            raise ValueError(msg)
        return stripped


class AcceptanceCriteria(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[AcceptanceCriterion] = Field(..., min_length=1)

    @field_validator("criteria")
    @classmethod
    def _reject_duplicate_ids(
        cls, criteria: list[AcceptanceCriterion]
    ) -> list[AcceptanceCriterion]:
        seen: set[str] = set()
        for criterion in criteria:
            lowered = criterion.id.lower()
            if lowered in seen:
                msg = f"duplicate acceptance_criterion id (case-insensitive): {criterion.id!r}"
                raise ValueError(msg)
            seen.add(lowered)
        return criteria


__all__ = ["AcceptanceCriteria", "AcceptanceCriterion"]
