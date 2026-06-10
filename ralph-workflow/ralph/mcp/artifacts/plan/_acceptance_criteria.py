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
    satisfied_by_steps: list[int] = Field(default_factory=list)

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

    @field_validator("satisfied_by_steps", mode="before")
    @classmethod
    def _validate_satisfied_by_steps(cls, value: object) -> list[int]:
        if not isinstance(value, list):
            msg = "satisfied_by_steps must be a list of integers"
            raise ValueError(msg)
        cleaned: list[int] = []
        seen: set[int] = set()
        for raw_entry in value:
            entry: object = raw_entry
            if isinstance(entry, bool) or not isinstance(entry, int):
                msg = f"satisfied_by_steps entry must be an int, got {type(entry).__name__}"
                raise ValueError(msg)
            assert isinstance(entry, int)
            if entry < 1:
                msg = "satisfied_by_steps entries must be positive integers"
                raise ValueError(msg)
            if entry in seen:
                continue
            seen.add(entry)
            cleaned.append(entry)
        return cleaned


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
