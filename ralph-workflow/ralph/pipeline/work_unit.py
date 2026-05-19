"""Single planning work unit declaration."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel

_UNIT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_UNIT_ID_MAX_LEN = 64
MAX_DESCRIPTION_CHARS = 4096


def _validate_relative_subpath(path: str) -> str:
    if not path:
        raise ValueError("allowed_directories entries must be non-empty")
    if "\\" in path:
        raise ValueError("allowed_directories entries must use '/' separators")
    parsed = PurePosixPath(path)
    if parsed.is_absolute():
        raise ValueError("allowed_directories entries must be relative paths")
    if ".." in parsed.parts:
        raise ValueError("allowed_directories entries must not contain '..'")
    return path


class WorkUnit(RalphBaseModel):
    """Single planning work unit declaration."""

    model_config = ConfigDict(frozen=True)

    unit_id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1, max_length=MAX_DESCRIPTION_CHARS)
    allowed_directories: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

    @field_validator("unit_id")
    @classmethod
    def _validate_unit_id(cls, v: str) -> str:
        if _UNIT_ID_RE.fullmatch(v):
            return v
        if not v:
            raise ValueError("unit_id must be 1-64 chars from [a-zA-Z0-9_-] (got empty string)")
        if len(v) > _UNIT_ID_MAX_LEN:
            raise ValueError(
                f"unit_id must be at most {_UNIT_ID_MAX_LEN} chars (got length {len(v)}: {v!r})"
            )
        invalid_char = next((ch for ch in v if not re.fullmatch(r"[a-zA-Z0-9_-]", ch)), None)
        if invalid_char is not None:
            raise ValueError(
                f"unit_id contains invalid character {invalid_char!r}; allowed: [a-zA-Z0-9_-]"
            )
        raise ValueError(f"unit_id must match ^[a-zA-Z0-9_-]{{1,64}}$ (got: {v!r})")

    @field_validator("allowed_directories")
    @classmethod
    def _validate_allowed_directories(cls, v: list[str]) -> list[str]:
        return [_validate_relative_subpath(path) for path in v]
