"""Structured smoke_test_result artifact validation helpers."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:
    from pathlib import Path

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.smoke_test_result_validation_error import SmokeTestResultValidationError
from ralph.pydantic_compat import RalphBaseModel
from ralph.pydantic_validation_errors import format_validation_error_messages

SMOKE_TEST_RESULT_ARTIFACT_TYPE = "smoke_test_result"


class SmokeTestResult(RalphBaseModel):
    """Validated schema for a smoke_test_result artifact."""

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


def normalize_smoke_test_result_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw smoke_test_result content dict."""
    try:
        validated = SmokeTestResult.model_validate(content)
        return validated.model_dump(mode="python", exclude_none=True)
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise SmokeTestResultValidationError(
            msgs[0] if len(msgs) == 1 else "\n".join(msgs) if msgs else str(exc)
        ) from exc


def read_smoke_test_result_artifact(repo_root: Path) -> dict[str, object] | None:
    """Read and validate the persisted smoke_test_result artifact content from the workspace.

    Returns ``None`` when the artifact file does not exist, cannot be read,
    or fails schema validation against :class:`SmokeTestResult`.  This prevents
    a malformed or incomplete artifact from influencing pass/fail decisions.
    """
    artifact_path = (
        repo_root / ".agent" / "artifacts" / f"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}.md"
    )
    try:
        import_module("ralph.mcp.artifacts.markdown.specs")
        markdown = artifact_path.read_text(encoding="utf-8")
        content, diagnostics = parse_and_validate(
            markdown,
            get_spec(SMOKE_TEST_RESULT_ARTIFACT_TYPE),
        )
    except (OSError, ValueError):
        return None
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return None
    normalized = {str(key): value for key, value in content.items()}
    try:
        normalize_smoke_test_result_content(normalized)
    except SmokeTestResultValidationError:
        return None
    return normalized


__all__ = [
    "SMOKE_TEST_RESULT_ARTIFACT_TYPE",
    "SmokeTestResultValidationError",
    "normalize_smoke_test_result_content",
    "read_smoke_test_result_artifact",
]
