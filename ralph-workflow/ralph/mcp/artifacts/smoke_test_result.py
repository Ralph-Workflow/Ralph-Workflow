"""Structured smoke_test_result artifact validation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:
    from pathlib import Path

from ralph.mcp.artifacts.store import get_artifact
from ralph.pydantic_compat import RalphBaseModel

SMOKE_TEST_RESULT_ARTIFACT_TYPE = "smoke_test_result"


class SmokeTestResult(RalphBaseModel):
    """Validated schema for a smoke_test_result artifact."""

    class SmokeTestResultValidationError(ValueError):
        """Raised when a smoke_test_result artifact is malformed."""


    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    output_file: str = Field(..., min_length=1)
    observed_working: list[str] = Field(default_factory=list)
    observed_breaks: list[str] = Field(default_factory=list)
    headless_guide_checks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_requirements(self) -> SmokeTestResult:
        if self.status not in {"passed", "failed", "partial"}:
            raise ValueError("status must be one of: passed, failed, partial")
        if self.status == "failed" and not self.observed_breaks:
            raise ValueError("failed smoke_test_result artifacts must include observed_breaks")
        if not self.headless_guide_checks:
            raise ValueError("smoke_test_result artifacts must include headless_guide_checks")
        return self


SmokeTestResultValidationError = SmokeTestResult.SmokeTestResultValidationError


def normalize_smoke_test_result_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw smoke_test_result content dict."""
    try:
        validated = SmokeTestResult.model_validate(content)
        return validated.model_dump(mode="python", exclude_none=True)
    except ValidationError as exc:
        raise SmokeTestResultValidationError(str(exc)) from exc


def read_smoke_test_result_artifact(repo_root: Path) -> dict[str, object] | None:
    """Read the persisted smoke_test_result artifact content from the workspace."""
    artifact_dir = repo_root / ".agent" / "artifacts"
    try:
        artifact = get_artifact(artifact_dir, SMOKE_TEST_RESULT_ARTIFACT_TYPE)
    except Exception:
        return None
    payload = artifact.content
    return {str(key): value for key, value in payload.items()}


__all__ = [
    "SMOKE_TEST_RESULT_ARTIFACT_TYPE",
    "SmokeTestResultValidationError",
    "normalize_smoke_test_result_content",
    "read_smoke_test_result_artifact",
]
