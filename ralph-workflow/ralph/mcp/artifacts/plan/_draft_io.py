"""Plan-draft and plan-artifact on-disk I/O.

The draft is a JSON file at ``.agent/artifacts/.plan_draft.json`` that
accumulates section submissions during a step-wise planning session.
The finalized plan is a JSON file at ``.agent/artifacts/plan.json`` that
holds the validated ``PlanArtifact`` content. Both reads and writes go
through a ``FileBackend`` so the production code can be unit-tested
with an in-memory backend instead of the real filesystem.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.plan._section_registry import (
    PLAN_ARTIFACT_PATH,
    PLAN_DRAFT_PATH,
    PLAN_DRAFT_SCHEMA_VERSION,
)
from ralph.mcp.artifacts.plan._validation import (
    PlanArtifactValidationError,
    normalize_plan_artifact_content,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.artifacts.plan._section_models import PlanArtifactDict


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def new_plan_draft(*, now_iso: Callable[[], str] = _now_iso) -> dict[str, object]:
    """Return a fresh plan draft with empty sections and timestamps."""
    now = now_iso()
    return {
        "schema_version": PLAN_DRAFT_SCHEMA_VERSION,
        "started_at": now,
        "updated_at": now,
        "sections": {},
    }


def load_plan_draft(
    artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> dict[str, object] | None:
    """Read the plan draft file if present and parseable. None otherwise."""
    draft_path = artifact_dir / ".plan_draft.json"
    if not backend.exists(draft_path):
        return None
    try:
        raw = backend.read_text(draft_path, encoding="utf-8")
        parsed = cast("object", json.loads(raw))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read plan draft at {}: {}", draft_path, exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("Plan draft at {} is not a JSON object", draft_path)
        return None
    parsed_dict = cast("dict[str, object]", parsed)
    if not isinstance(parsed_dict.get("sections"), dict):
        logger.warning("Plan draft at {} has no 'sections' object", draft_path)
        return None
    return parsed_dict


def save_plan_draft(
    artifact_dir: Path,
    draft: dict[str, object],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    now_iso: Callable[[], str] = _now_iso,
) -> None:
    """Atomically write the plan draft file."""
    backend.mkdir(artifact_dir, parents=True, exist_ok=True)
    draft_path = artifact_dir / ".plan_draft.json"
    tmp_path = draft_path.with_suffix(".json.tmp")
    serialized_draft = dict(draft)
    serialized_draft["updated_at"] = now_iso()
    serialized = json.dumps(serialized_draft, indent=2, sort_keys=False)
    backend.write_text(tmp_path, serialized, encoding="utf-8")
    backend.replace(tmp_path, draft_path)


def delete_plan_draft(artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND) -> bool:
    """Remove the plan draft file. Returns True if it existed."""
    draft_path = artifact_dir / ".plan_draft.json"
    if not backend.exists(draft_path):
        return False
    backend.unlink(draft_path)
    return True


def load_plan_artifact_sections(
    artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> PlanArtifactDict | None:
    """Load the normalized sections from a finalized plan artifact if present."""
    plan_path = artifact_dir / "plan.json"
    if not backend.exists(plan_path):
        return None

    result: PlanArtifactDict | None = None
    try:
        raw = backend.read_text(plan_path, encoding="utf-8")
        parsed = cast("object", json.loads(raw))
        if not isinstance(parsed, dict):
            logger.warning("Plan artifact at {} is not a JSON object", plan_path)
            return None
        parsed_dict = cast("dict[str, object]", parsed)
        content = parsed_dict.get("content") if parsed_dict.get("type") == "plan" else parsed_dict
        if not isinstance(content, dict):
            logger.warning("Plan artifact at {} has no valid 'content' object", plan_path)
            return None
        normalized = normalize_plan_artifact_content(cast("dict[str, object]", content))
        if normalized.get("noop") is not True:
            result = normalized
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read plan artifact at {}: {}", plan_path, exc)
    except PlanArtifactValidationError as exc:
        logger.warning(
            "Plan artifact at {} failed validation for draft hydration: {}",
            plan_path,
            exc,
        )

    return result


__all__ = [
    "PLAN_ARTIFACT_PATH",
    "PLAN_DRAFT_PATH",
    "delete_plan_draft",
    "load_plan_artifact_sections",
    "load_plan_draft",
    "new_plan_draft",
    "save_plan_draft",
]
