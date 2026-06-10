"""Plan step sub-model.

The ``satisfies`` / ``expected_evidence`` / ``verify_command`` fields turn each
step into an executor-ready unit with an explicit completion contract. The
``_validate_step_type_contract`` model-level validator enforces that every
``file_change`` step declares at least one ``targets`` entry and every
``verify`` step declares either ``verify_command`` or ``location``.

The step type is a ``StepType`` StrEnum (see ``_step_contract``) so the
closed set of kinds is self-documenting and the per-step contract helpers
(``requires_targets`` / ``requires_verify_handle``) can be consulted
instead of pattern-matching literal strings.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from ralph.mcp.artifacts.plan._step_contract import (
    StepType,
    requires_targets,
    requires_verify_handle,
)
from ralph.mcp.artifacts.plan._step_target import StepTarget
from ralph.pydantic_compat import RalphBaseModel

_ACCEPTANCE_CRITERION_ID_PATTERN = re.compile(r"^[A-Z]+-\d{2,}$")
_MAX_EVIDENCE_ENTRIES = 50
_MAX_EVIDENCE_ENTRY_LENGTH = 200


class PlanStep(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    step_type: StepType = StepType.ACTION
    priority: Literal["critical", "high", "medium", "low"] | None = None
    targets: list[StepTarget] = Field(default_factory=list)
    location: str | None = None
    rationale: str | None = None
    depends_on: list[int] = Field(default_factory=list)
    satisfies: list[str] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    verify_command: str | None = None

    @field_validator("satisfies")
    @classmethod
    def _validate_satisfies(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for entry in value:
            stripped = entry.strip()
            if not stripped:
                msg = "satisfies entries must not be empty"
                raise ValueError(msg)
            if not _ACCEPTANCE_CRITERION_ID_PATTERN.match(stripped):
                msg = (
                    f"satisfies entry {stripped!r} does not match the AC id pattern "
                    r"^[A-Z]+-\d{2,}$"
                )
                raise ValueError(msg)
            cleaned.append(stripped)
        return cleaned

    @field_validator("expected_evidence")
    @classmethod
    def _validate_expected_evidence(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        position_by_lowered: dict[str, int] = {}
        for entry in value:
            stripped = entry.strip()
            if not stripped:
                continue
            if len(stripped) > _MAX_EVIDENCE_ENTRY_LENGTH:
                msg = f"expected_evidence entry exceeds {_MAX_EVIDENCE_ENTRY_LENGTH} chars"
                raise ValueError(msg)
            lowered = stripped.lower()
            if lowered in position_by_lowered:
                cleaned[position_by_lowered[lowered]] = stripped
                continue
            position_by_lowered[lowered] = len(cleaned)
            cleaned.append(stripped)
        if len(cleaned) > _MAX_EVIDENCE_ENTRIES:
            msg = f"expected_evidence has more than {_MAX_EVIDENCE_ENTRIES} entries"
            raise ValueError(msg)
        return cleaned

    @field_validator("verify_command")
    @classmethod
    def _validate_verify_command(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            msg = "verify_command must not be empty when provided"
            raise ValueError(msg)
        return stripped

    @model_validator(mode="after")
    def _validate_step_type_contract(self) -> PlanStep:
        if requires_targets(self.step_type) and len(self.targets) == 0:
            msg = "file_change step must declare at least one target"
            raise ValueError(msg)
        if (
            requires_verify_handle(self.step_type)
            and self.verify_command is None
            and self.location is None
        ):
            msg = "verify step must declare verify_command or location"
            raise ValueError(msg)
        return self


__all__ = ["PlanStep"]
