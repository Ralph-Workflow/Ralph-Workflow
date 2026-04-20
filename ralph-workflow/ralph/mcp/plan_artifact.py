"""Backward-compatible plan artifact helpers re-exported from the sub-package."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.plan import (
    PLAN_ARTIFACT_PATH,
    PLAN_ARTIFACT_TYPE,
    PLAN_DRAFT_PATH,
    PLAN_DRAFT_SCHEMA_VERSION,
    PLAN_SECTION_LIST_ITEM_MODELS,
    PLAN_SECTION_NAMES,
    PLAN_SECTION_OBJECT_MODELS,
    PlanArtifact,
    PlanArtifactValidationError,
    SectionMode,
    delete_plan_draft,
    finalize_plan_draft,
    is_noop_plan,
    load_plan_draft,
    merge_plan_section,
    new_plan_draft,
    normalize_plan_artifact_content,
    save_plan_draft,
    validate_plan_section,
)

if TYPE_CHECKING:
    from pathlib import Path


def get_plan_draft(
    artifact_dir: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> dict[str, object] | None:
    """Backward-compatible alias for ``load_plan_draft``."""
    return load_plan_draft(artifact_dir, backend=backend)



def validate_plan_artifact(content: dict[str, object]) -> dict[str, object]:
    """Backward-compatible alias for ``normalize_plan_artifact_content``."""
    return normalize_plan_artifact_content(content)


__all__ = [
    "PLAN_ARTIFACT_PATH",
    "PLAN_ARTIFACT_TYPE",
    "PLAN_DRAFT_PATH",
    "PLAN_DRAFT_SCHEMA_VERSION",
    "PLAN_SECTION_LIST_ITEM_MODELS",
    "PLAN_SECTION_NAMES",
    "PLAN_SECTION_OBJECT_MODELS",
    "PlanArtifact",
    "PlanArtifactValidationError",
    "SectionMode",
    "delete_plan_draft",
    "finalize_plan_draft",
    "get_plan_draft",
    "is_noop_plan",
    "load_plan_draft",
    "merge_plan_section",
    "new_plan_draft",
    "normalize_plan_artifact_content",
    "save_plan_draft",
    "validate_plan_artifact",
    "validate_plan_section",
]
